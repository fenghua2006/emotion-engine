"""L2 慢通道参考线 —— trust / love / longing / guilt。

L2 不是新的生产入口，而是把 L0 的纯函数按 ``engine.tick()`` / ``wake()``
的慢通道路径重新组合成一条可复现的参考线，用于：

  - 固定人格、固定时钟的 slow golden；
  - trust / love / longing / guilt 的独立不变量测试；
  - slow → fast 交互的前后对照；
  - L3 / L3.5 关闭新层时的“旧曲线不变”对照。

刻意不包含：
  - tolerance / 伤疤 / 氛围 / 记忆 / Saga / LLM；
  - 生产引擎的 atmosphere / shock 顺序问题（L3）。

L2 基线口径（2026-10-26）:
  - fast + guilt：人格基线；
  - trust：``trust_target(love)``，关系均衡线，不是人格基线；
  - love：人格基线，高于底色才算“额外依恋”；
  - trust 对 fear / anger / disgust 的绝对缓冲仍由 ``gate_appraisal``
    负责，不依赖交互矩阵。

legacy 开关:
  - ``legacy_slow=True``：guilt 无 0.3 门槛；love 使用累计正面事件；
    trust 交互回到人格基线且不设 trust_target 上限。
  - ``legacy_interactions=True``：交互矩阵回到绝对阈值语义。
  两者同时为 True 时最接近 L1.1 之前的旧曲线。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from core import (
    Appraisal,
    Channel,
    EmotionalState,
    FAST_CHANNELS,
    GUILT_SELF_AGENCY_THRESHOLD,
    HALF_LIFE,
    OFFLINE_COMPRESSION,
    OFFLINE_TRUST_DECAY_DAYS,
    OFFLINE_TRUST_DECAY_RATE,
    Personality,
    SHOCK_WEIGHT,
    apply_interactions,
    appraise,
    damage_trust,
    decay,
    gate_appraisal,
    grow_slow_channels,
    interaction_baselines,
    return_to_baseline,
    saturate,
    trust_target,
)

# 表达层能读到的“主导情绪”候选。trust / love 是关系量，不作为表情标签。
DOMINANT_CANDIDATES: Tuple[Channel, ...] = (
    Channel.JOY,
    Channel.SADNESS,
    Channel.ANGER,
    Channel.FEAR,
    Channel.SURPRISE,
    Channel.LONGING,
    Channel.GUILT,
)
DOMINANT_THRESHOLD = 0.3

# 与 engine 的 appraisal 写入范围一致：六快通道 + guilt。
APPRAISAL_CHANNELS: Tuple[Channel, ...] = FAST_CHANNELS


@dataclass(frozen=True)
class L2Step:
    """一条 L2 参考线 tick 的可比较结果。"""

    key: str
    mode: str                       # "online" / "offline"
    values: Dict[str, float]
    dominant: str
    shock_channels: Tuple[str, ...]
    blends: Tuple[str, ...]
    raw_appraisal: Dict[str, float]
    gated_appraisal: Dict[str, float]
    trust_target: float
    slow_positive_input: int


@dataclass
class SlowLine:
    """固定人格、固定时钟的十通道参考线。"""

    personality: Personality = field(default_factory=Personality)
    legacy_slow: bool = False
    legacy_interactions: bool = False
    state: EmotionalState = field(init=False)
    positive_events: int = field(init=False, default=0)
    events: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """回到人格基线；trust / love 也从各自基线出发。"""
        baseline = self.personality.baseline()
        self.state = EmotionalState()
        for ch in Channel:
            setattr(self.state, ch.value, baseline[ch])
        self.state.longing = 0.0
        self.state.guilt = 0.0
        self.state._previous = None
        self.positive_events = 0
        self.events = 0

    def _guard_non_finite(self) -> None:
        baseline = self.personality.baseline()
        for ch in Channel:
            value = getattr(self.state, ch.value)
            if not math.isfinite(value):
                setattr(self.state, ch.value, baseline[ch])

    def _positive_now(self, appraisal: Optional[Appraisal]) -> int:
        if (appraisal is not None
                and appraisal.goal_conduciveness > 0
                and appraisal.other_agency > 0):
            return 1
        return 0

    def _snapshot(self, key: str, mode: str,
                  raw: Optional[Dict[Channel, float]] = None,
                  gated: Optional[Dict[Channel, float]] = None,
                  blends: Optional[List[str]] = None,
                  shock: Optional[List[str]] = None,
                  positive_input: int = 0) -> L2Step:
        values = {ch.value: getattr(self.state, ch.value) for ch in Channel}
        return L2Step(
            key=key,
            mode=mode,
            values=values,
            dominant=dominant_channel(self.state),
            shock_channels=tuple(shock or ()),
            blends=tuple(blends or ()),
            raw_appraisal={ch.value: (raw or {}).get(ch, 0.0)
                           for ch in APPRAISAL_CHANNELS},
            gated_appraisal={ch.value: (gated or {}).get(ch, 0.0)
                             for ch in APPRAISAL_CHANNELS},
            trust_target=trust_target(self.state.love),
            slow_positive_input=positive_input,
        )

    # ── 在线 ─────────────────────────────────────────────

    def step(self,
             appraisal: Optional[Appraisal] = None,
             elapsed_minutes: float = 10.0,
             boundary_violation: bool = False,
             key: str = "") -> L2Step:
        """一次在线 tick，按 engine.tick() 的纯动力学路径更新。"""
        elapsed_minutes = max(0.0, elapsed_minutes)
        elapsed_hours = elapsed_minutes / 60.0
        self._guard_non_finite()
        self.state.snapshot_previous()
        baseline = self.personality.baseline()

        # Step 1: 思念在线消退。
        if self.state.longing > 0:
            longing_decay = 0.3 * elapsed_hours if elapsed_hours > 0 else 0.05
            floor = self.state.love * 0.05
            self.state.longing = max(
                floor, self.state.longing - longing_decay,
            )

        # Step 2: 快通道 + guilt 的弹性衰减与回弹。
        for ch in FAST_CHANNELS:
            value = getattr(self.state, ch.value)
            value = decay(value, HALF_LIFE[ch], elapsed_minutes, baseline[ch])
            value = return_to_baseline(value, baseline[ch], elapsed_minutes)
            setattr(self.state, ch.value, value)

        # Step 3: 慢通道生长。L2 只喂本 tick 的正面事件增量。
        positive_now = self._positive_now(appraisal)
        positive_input = (
            self.positive_events if self.legacy_slow else positive_now
        )
        grow_slow_channels(
            self.state, self.events, positive_input, elapsed_hours,
        )

        # Step 4: 认知评估 → 门控 → 饱和写入。
        raw: Dict[Channel, float] = {}
        gated: Dict[Channel, float] = {}
        if appraisal is not None:
            raw = appraise(
                appraisal,
                guilt_self_agency_threshold=(
                    0.0 if self.legacy_slow
                    else GUILT_SELF_AGENCY_THRESHOLD
                ),
            )
            gated = gate_appraisal(
                raw, self.state.trust, self.state.love,
                boundary_violation=boundary_violation,
            )
            for ch in APPRAISAL_CHANNELS:
                activation = gated.get(ch, 0.0)
                if activation > 0.001:
                    current = getattr(self.state, ch.value)
                    setattr(
                        self.state, ch.value,
                        current + activation * saturate(current),
                    )

            # 背叛：信任腰斩；严重时二级侵蚀 love。
            if (appraisal.goal_conduciveness < -0.5
                    and appraisal.other_agency > 0.5):
                damage_trust(
                    self.state, abs(appraisal.goal_conduciveness),
                )

            self.events += 1
            self.positive_events += positive_now

        # 越界惩罚：低信任时被越界称呼。与 engine 一样，不依赖本 tick
        # 是否有 appraisal。
        if boundary_violation and self.state.trust < 0.5:
            self.state.trust = max(
                0.01,
                self.state.trust
                - (0.5 - self.state.trust) * 0.3,
            )

        # Step 5: 交互矩阵每个 tick 都跑（与 engine 一致）。L2 使用
        # trust_target 作为 trust 的交互基线；love 仍用他人格基线。
        # legacy 组合可复现旧行为。
        if self.legacy_slow:
            interaction_baseline = baseline
            ceiling = None
        else:
            interaction_baseline = interaction_baselines(
                self.personality, self.state.love,
            )
            ceiling = trust_target(self.state.love)
        if self.legacy_interactions:
            interaction_baseline = None

        blends = apply_interactions(
            self.state,
            baseline=interaction_baseline,
            trust_ceiling=ceiling,
        )

        # Step 6: 冲击检测（与 engine 相同的指数 Δ 规则）。
        shock: List[str] = []
        for ch in FAST_CHANNELS:
            raw_delta = abs(self.state.delta(ch))
            clamped = min(raw_delta, 3.0)
            if math.exp(clamped * 2.0) - 1 > SHOCK_WEIGHT.get(ch, 0.3):
                shock.append(ch.value)

        return self._snapshot(
            key=key, mode="online",
            raw=raw, gated=gated, blends=blends, shock=shock,
            positive_input=positive_input,
        )

    # ── 离线 ─────────────────────────────────────────────

    def offline(self, hours: float, key: str = "") -> L2Step:
        """一次离线结算，按 engine.wake() 的慢通道路径更新。"""
        hours = max(0.0, hours)
        offline_minutes = hours * 60.0
        self._guard_non_finite()
        self.state.snapshot_previous()
        baseline = self.personality.baseline()

        # 快通道：压缩时间衰减（不额外做基线回弹，与 wake 一致）。
        for ch in FAST_CHANNELS:
            comp = OFFLINE_COMPRESSION.get(ch, 0.3)
            value = getattr(self.state, ch.value)
            value = decay(
                value, HALF_LIFE[ch], offline_minutes * comp, baseline[ch],
            )
            setattr(self.state, ch.value, value)

        # 慢通道：离线期间也有微量生长。
        grow_slow_channels(self.state, 0, 0, hours)

        # 思念：离线生长，受 love 上限约束。
        if hours > 1 and self.state.love > 0.05:
            growth = (
                self.state.love
                * math.log10(1 + hours)
                * 0.4
            )
            self.state.longing = min(
                self.state.longing + growth,
                self.state.love * 2,
            )

        # trust 长时间不联系微降。
        offline_days = hours / 24.0
        if offline_days > OFFLINE_TRUST_DECAY_DAYS:
            excess = offline_days - OFFLINE_TRUST_DECAY_DAYS
            weeks = excess / 7.0
            self.state.trust *= OFFLINE_TRUST_DECAY_RATE ** weeks

        return self._snapshot(key=key, mode="offline")

    # ── 批量 ─────────────────────────────────────────────

    def run(self, events: Iterable) -> List[L2Step]:
        """按顺序跑一组 L2Event；离线事件调用 offline()。"""
        steps: List[L2Step] = []
        for event in events:
            if getattr(event, "offline_hours", 0.0) > 0:
                steps.append(
                    self.offline(event.offline_hours, key=event.key)
                )
            else:
                steps.append(
                    self.step(
                        event.appraisal,
                        elapsed_minutes=event.elapsed_minutes,
                        boundary_violation=event.boundary_violation,
                        key=event.key,
                    )
                )
        return steps


def dominant_channel(state: EmotionalState) -> str:
    """表达层候选通道中原始值最高者；不超过 0.3 视为 neutral。"""
    best = max(DOMINANT_CANDIDATES, key=lambda ch: getattr(state, ch.value))
    value = getattr(state, best.value)
    return best.value if value > DOMINANT_THRESHOLD else "neutral"
