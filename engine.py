"""
Emotion Engine v0.8-dev — 多通道情绪交互引擎（L0 核心·L2 慢通道·记忆·耐受窗·JEV/MCP·L7 感染）
==================================================================
论文验证的设计决策:
  - Jennings (2025): 情绪 = 多个并行匹配流同时激活
  - Diamond (2007) / Bowen (2016): Arousal 驱动记忆固化，非 Valence
  - Nielson (2007): 30分钟记忆固化窗口
  - Sci Reports (2024): 抑郁现实主义（高 sadness 者回忆更准）
  - Vanderbilt: 混合情绪中各组分互不消灭
  - Self-Excited Dynamics (2024): 消极情绪留存更久

作者: 枫骅 & SCC
日期: 2026/06/30（v0.7-dev 更新: 2026/10/26）

v0.7-dev 核心变更:
  - 耐受窗（tolerance.py）: load / capacity / regulated-hyper-hypo
  - 负荷只取负性通道相对基线的偏离，trust/love 改为扩展容量
  - 两 tick 滞回；hyper 放大负性输入并拖慢恢复，hypo 降低反应
  - 快照完整持久化: 通道 + 耐受窗 + 事件计数 + Saga 计时 + shock 历史
  - tick()/wake() 支持注入 now，便于确定性测试

L0 核心提取（2026/10/25）:
  - 纯动力学拆到 core.py：不依赖记忆 / 耐受 / 伤疤 / 氛围 / LLM
  - tick(trace=True) 返回 StepTrace 分阶段快照，不改变计算
  - 旧 import 路径保持；69 个 unittest（含 28 个 core 测试）
  - 详细契约见 docs/CORE.md

L1.1 交互激活修复（2026/10/26）:
  - 交互矩阵默认由“高于人格基线的激活”驱动，不再把基线当情绪
  - 快通道放大规则要求目标共激活；抑制规则不要求
  - legacy_interactions=True 保留 v0.7 绝对阈值行为
  - 空闲 300 tick 回到基线；纯负面事件不再凭空加 joy
  - 详细记录见 docs/L1_FAST_CHANNELS.md

L2 慢通道语义（2026/10/26）:
  - slow → fast 基线：trust 用 trust_target(love)，love / 快通道 / guilt 用
    人格基线；正常关系中的 trust 不会在空闲 tick 持续压 fear
  - guilt 只在 self_agency > 0.3 时激活，低于门槛严格为 0
  - love 的正面事件增量改为按本 tick 新增，不再用累计 positive_events
  - trust ∈ [0, 1]、love ∈ [0, 2]；love→trust / guilt→trust 的累计增量
    受 trust_target(love) 封顶
  - legacy_slow_channels=True 保留旧 guilt / 累计正面事件 / trust 基线行为
  - 详细记录见 docs/L2_SLOW_CHANNELS.md

L3 氛围与伤疤（2026/10/26）:
  - 氛围调制移到 state 写入之前，本 tick 的 gated 真正受氛围影响
  - 先检测 shock，再用非零 shock 密度更新氛围；新氛围作用于下一 tick
  - atmosphere 在 corrected 模式限制在 [-1, 1]
  - 伤疤只放大 sadness / fear / anger，effective shift 上限 0.6
  - legacy_atmosphere=True 保留 pre-L3 的顺序、零密度、无 clamp 与旧 notes
  - 详细记录见 docs/L3_ATMOSPHERE_SCARS.md

L4 关系域记忆（2026/10/26）:
  - 六维 appraisal prior 通过 tick(appraisal_bias=...) 进入评估，每维 ±0.2
  - 仅在调用方显式传入 Appraisal 时生效；记忆永不写 Personality / CharacterCore
  - 十通道 memory_bias()、关系摘要自动刷新、写入闸门 v2、旧库幂等迁移
  - 默认 trace 键集合不变；传入先验时追加 memory_bias_* 观测字段
  - 详细记录见 docs/MEMORY_SYSTEM.md

L6 表达 / JEV / Noul / MCP（2026/10/27）:
  - respond_llm(use_jev=...)：文本经 jev.py 生成 Appraisal，再走原 tick()
  - auto_boundary：noul.py 只提供边界建议，显式 boundary_violation 优先
  - 新参数全部默认关闭；失败写 _jev_error / _boundary_error，主链路继续
  - MCP 外壳与 trace 回放在 mcp_server.py / scripts/replay_trace.py，不改动力学
  - 详细记录见 docs/JEV.md、docs/MCP.md

L7 多角色情绪感染 v0 + v0.1 + v0.2（2026/10/27）:
  - contagion.py：A 的四维 Appraisal → B 的有界 appraisal 先验
  - tick(contagion_bias=...) 在记忆先验之后、appraise 之前叠加
  - v0：ContagionPair 先 tick A 再 tick B；默认关闭，旧路径逐位不变
  - v0.1：strength 关系强度 + tick_bidirectional 双向（先算两侧偏置再 tick）
  - v0.2：relationship_strength() 从 trust / love 自动推导 strength
  - 详细记录见 docs/L7_MULTI_CHARACTER.md

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
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from tolerance import (
    CAPACITY_MODEL_MULTI,
    CAPACITY_MODEL_SCALAR,
    VALID_CAPACITY_MODELS,
    ZONE_HYPER,
    ZONE_HYPO,
    ToleranceConfig,
    ToleranceState,
)

from atmosphere import (
    MAX_SCAR_SHIFT,
    apply_atmosphere_modulation,
    apply_scar_amplification,
    clamp_atmosphere,
    clamp_scar_shift,
    drift_atmosphere,
)


from core import (
    ABSENCE_CHANNELS,  # noqa: F401 — 兼容旧 import
    APPRAISAL_LIMITS,
    Appraisal,
    Channel,
    EmotionalState,
    FAST_CHANNELS,
    GUILT_SELF_AGENCY_THRESHOLD,
    HALF_LIFE,
    INTERACTION_RULES,  # noqa: F401 — 兼容旧 import
    NEGATIVE_CHANNELS,
    NEGATIVE_CHANNEL_NAMES,  # noqa: F401 — 兼容旧 import
    OFFLINE_COMPRESSION,
    OFFLINE_TRUST_DECAY_DAYS,
    OFFLINE_TRUST_DECAY_RATE,
    Personality,
    SHOCK_WEIGHT,
    SLOW_CHANNELS,  # noqa: F401 — 兼容旧 import
    StepTrace,
    apply_appraisal_bias,
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

from contagion import (
    CONTAGION_DIMENSIONS,
    MAX_CONTAGION_CAP,
    sanitize_contagion_bias,
)

from meta_emotion import (
    MetaEmotionProfile,
    evaluate_meta_emotion,
)

from expression import (
    _CHARACTER_POOLS,  # noqa: F401 — 兼容旧 import
    _ZONE_POOLS,  # noqa: F401 — 兼容旧 import
    _action_tendency,
    _build_llm_prompt,
    _dominant_channel,
    _emotion_snapshot,
    _expression_params,
    _mood_hint,  # noqa: F401 — 兼容旧 import
    _select_local_utterance,
)
from llm_client import call_llm


@dataclass
class _TickContext:
    """一次 tick 的中间状态；只用于组合根拆分，不对外暴露。"""

    now: float
    raw_elapsed_minutes: float
    elapsed_minutes: float
    elapsed_hours: float
    appraisal: Optional[Appraisal]
    boundary_violation: bool
    appraisal_bias: Optional[Dict[str, float]]
    contagion_bias: Optional[Dict[str, float]]
    trace_data: Optional[StepTrace]
    baseline: Dict[Channel, float]
    atmosphere_before: float
    positive_input: int = 0
    raw: Dict[Channel, float] = field(default_factory=dict)
    gated: Dict[Channel, float] = field(default_factory=dict)
    shock_channels: List[str] = field(default_factory=list)
    gated_written: Dict[Channel, float] = field(default_factory=dict)
    scar_shift: float = 0.0
    scar_channels: List[str] = field(default_factory=list)
    atmosphere_applied: float = 0.0
    atmosphere_multipliers: Dict[str, float] = field(default_factory=dict)
    shock_density: float = 0.0
    atmosphere_arousal: float = 0.0
    appraisal_before_memory: Dict[str, float] = field(default_factory=dict)
    memory_bias_applied: Dict[str, float] = field(default_factory=dict)
    contagion_enabled: bool = False
    contagion_applied: Dict[str, float] = field(default_factory=dict)
    appraisal_before_contagion: Dict[str, float] = field(default_factory=dict)
    blends: List[str] = field(default_factory=list)
    meta_state: Optional[Any] = None
    meta_load_bias: float = 0.0


@dataclass
class EmotionEngine:
    """一次一个 Agent 的实例。"""
    state:       EmotionalState
    personality: Personality
    memory:      "MemoryStore"
    scars:       "SensitizationStore"
    events_today:       int = 0
    positive_events:    int = 0
    _last_saga:         float = field(default_factory=time.time)
    atmosphere:         float = -0.3  # -1 放松 ~ +1 紧绷。初始偏轻松。
    _recent_shocks:     List[float] = field(default_factory=list)  # 最近 N 个事件的 shock 强度
    tolerance:          ToleranceState = field(default_factory=ToleranceState)
    # L1.1: 交互矩阵默认由“高于人格基线的激活”驱动，消除空闲交互地板。
    # 设为 True 可复现 v0.7 的绝对阈值行为，用于旧曲线对照。
    legacy_interactions: bool = False
    # L2: 设为 True 可复现旧的慢通道行为：
    #   - guilt 只要 self_agency > 0 就激活（无 0.3 门槛）
    #   - love 增长使用累计 positive_events 而不是本次新增
    #   - trust 的正向交互不设 trust_target 上限
    # 用于信任/爱意长程曲线的前后对照。
    legacy_slow_channels: bool = False
    # L3: False = 修正后的氛围顺序 / 非零 shock 密度 / [-1,1] clamp；
    # True = 复现 pre-L3 的晚调制、零密度、无 clamp 与旧 trace notes。
    legacy_atmosphere: bool = False
    # L3.5: v0 元情绪只做 trace 观测，默认关闭。单独传 profile 不会自动开启；
    # 必须显式 meta_emotion_enabled=True。profile 是角色配置，不写入快照。
    meta_emotion_enabled: bool = False
    meta_emotion_profile: Optional[MetaEmotionProfile] = None
    # L5: 多域容量默认开启；legacy_tolerance 把配置与状态都切回旧标量模型。
    tolerance_config: Optional[ToleranceConfig] = None
    legacy_tolerance: bool = False
    # L5: 默认只让 meta 情绪写 trace；显式开启后才消费有界 load bias。
    tolerance_meta_load_enabled: bool = False

    def __post_init__(self) -> None:
        config = self.tolerance_config or self.tolerance.config
        if config.capacity_model not in VALID_CAPACITY_MODELS:
            config = replace(config, capacity_model=CAPACITY_MODEL_MULTI)
        if self.legacy_tolerance:
            config = replace(config, capacity_model=CAPACITY_MODEL_SCALAR)
        self.tolerance_config = config
        self.tolerance.config = config
        self.tolerance.capacity_model = config.capacity_model

    def _scar_pressure(self) -> float:
        """旧伤疤对耐受窗的压缩程度。取最强的一条，不累加。"""
        strongest = max(
            (clamp_scar_shift(pattern.threshold_shift)
             for pattern in self.scars.patterns.values()),
            default=0.0,
        )
        return max(0.0, min(1.0, strongest))

    def _scar_pressure_by_tag(self) -> Dict[str, float]:
        """按事件类型返回伤疤压力；L5 多域容量按域取 max。"""
        return {
            tag: max(0.0, min(1.0, clamp_scar_shift(
                pattern.threshold_shift,
            )))
            for tag, pattern in self.scars.patterns.items()
        }

    def _update_tolerance(self, *,
                          shock_channels: Tuple[str, ...] = (),
                          boundary_violation: bool = False,
                          elapsed_hours: float = 0.0,
                          meta_load_bias: float = 0.0) -> str:
        """用当前通道状态刷新耐受窗。必须在通道更新之后调用。"""
        baseline = {ch.value: value for ch, value in self.personality.baseline().items()}
        scalar = (
            self.legacy_tolerance
            or self.tolerance.capacity_model == CAPACITY_MODEL_SCALAR
        )
        return self.tolerance.update(
            state=self.state.to_dict(),
            baseline=baseline,
            trust=self.state.trust,
            love=self.state.love,
            trust_target_value=trust_target(self.state.love),
            neuroticism=self.personality.neuroticism,
            atmosphere=self.atmosphere,
            shock_channels=shock_channels,
            boundary_violation=boundary_violation,
            scar_pressure=self._scar_pressure(),
            scar_pressure_by_tag=(
                None if scalar else self._scar_pressure_by_tag()
            ),
            meta_load_bias=0.0 if scalar else meta_load_bias,
            elapsed_hours=elapsed_hours,
        )

    def wake(self, now: Optional[float] = None) -> Dict:
        """
        从休眠中醒来。结算离线期间的情绪变化。
        离线时间对每个通道的"感受"不同——愤怒消散快，爱几乎不变。
        调用时机: Agent 启动/恢复对话时，tick() 之前调用一次。
        now 可注入，便于离线结算的确定性测试。
        """
        now = time.time() if now is None else now
        if not self.legacy_atmosphere:
            self.atmosphere = clamp_atmosphere(self.atmosphere)
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
            longing_growth = self.state.love * math.log10(1 + offline_hours) * 0.4
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

        self._update_tolerance(elapsed_hours=offline_hours)

        return {
            "offline_minutes":     round(offline_minutes, 1),
            "compressed_fast":     {ch.value: round(offline_minutes * OFFLINE_COMPRESSION.get(ch, 0.3), 1)
                                   for ch in FAST_CHANNELS},
            "state":              self.state.to_dict(),
            "tolerance":          self.tolerance.to_dict(),
        }

    def tick(self, appraisal: Optional[Appraisal] = None,
             boundary_violation: bool = False,
             now: Optional[float] = None,
             trace: bool = False,
             appraisal_bias: Optional[Dict[str, float]] = None,
             contagion_bias: Optional[Dict[str, float]] = None) -> Dict:
        """
        每次对话轮次/事件 的完整情绪更新。
        boundary_violation: 低信任时的越界称呼（亲密词等）
        now: 可注入的时钟，便于确定性测试；默认使用当前时间。
        trace: 为 True 时额外返回 StepTrace（分阶段快照，不改变计算）。
        appraisal_bias: L4 记忆先验；只叠加到六维 Appraisal，默认 None
            时计算路径与键集合完全不变。
        contagion_bias: L7 多角色感染先验；在记忆先验之后、appraise() 之前
            叠加，默认 None 时计算路径与键集合完全不变。

        实现被拆成带顺序的 _step_*；顺序即契约，改变顺序必须重跑
        L1/L2/L5 六套 golden 与 L3 A/B 测试。
        """
        ctx = self._begin_tick(
            appraisal=appraisal,
            boundary_violation=boundary_violation,
            now=now,
            trace=trace,
            appraisal_bias=appraisal_bias,
            contagion_bias=contagion_bias,
        )
        self._step_decay(ctx)
        self._step_slow_channels(ctx)
        self._step_appraisal(ctx)
        self._step_after_appraisal(ctx)
        self._step_interactions(ctx)
        self._step_shock(ctx)
        self._step_memory_and_saga(ctx)
        self._step_meta_and_tolerance(ctx)
        return self._build_tick_result(ctx)

    # ── tick 各阶段：顺序即契约 ──

    def _begin_tick(self, *,
                    appraisal: Optional[Appraisal],
                    boundary_violation: bool,
                    now: Optional[float],
                    trace: bool,
                    appraisal_bias: Optional[Dict[str, float]],
                    contagion_bias: Optional[Dict[str, float]]
                    ) -> _TickContext:
        now = time.time() if now is None else now
        raw_elapsed_minutes = (now - self.state._last_update) / 60.0
        elapsed_minutes = max(0.0, raw_elapsed_minutes)
        elapsed_hours = elapsed_minutes / 60.0

        # NaN/Inf 防护——极端值后重设
        for ch in Channel:
            val = getattr(self.state, ch.value)
            if math.isnan(val) or math.isinf(val):
                bl = self.personality.baseline()
                setattr(self.state, ch.value, bl[ch])

        # L3: corrected 模式在入口先收束氛围，避免异常快照/手动值越界。
        if not self.legacy_atmosphere:
            self.atmosphere = clamp_atmosphere(self.atmosphere)
        atmosphere_before = self.atmosphere

        trace_data: Optional[StepTrace] = None
        if trace:
            trace_data = StepTrace(elapsed_minutes=elapsed_minutes)
            trace_data.mark("before", self.state)
            trace_data.atmosphere_mode = (
                "legacy" if self.legacy_atmosphere else "corrected"
            )
            trace_data.atmosphere_before = atmosphere_before
            if raw_elapsed_minutes < 0:
                trace_data.notes.append(
                    "clock_went_backwards: elapsed clamped to 0"
                )

        # Step 0: 保存上一帧快照（对比度用）
        self.state.snapshot_previous()

        return _TickContext(
            now=now,
            raw_elapsed_minutes=raw_elapsed_minutes,
            elapsed_minutes=elapsed_minutes,
            elapsed_hours=elapsed_hours,
            appraisal=appraisal,
            boundary_violation=boundary_violation,
            appraisal_bias=appraisal_bias,
            contagion_bias=sanitize_contagion_bias(contagion_bias),
            trace_data=trace_data,
            baseline=self.personality.baseline(),
            atmosphere_before=atmosphere_before,
        )

    def _step_decay(self, ctx: _TickContext) -> None:
        """Step 1: 弹性衰减与思念消退。"""
        baseline = ctx.baseline

        # 思念在线时消退——人在身边，思有所归
        # 但有 love 时不会归零——爱着的人即使见面也隐约知道会再分开
        if self.state.longing > 0:
            longing_decay = (
                0.3 * ctx.elapsed_hours if ctx.elapsed_hours > 0 else 0.05
            )
            floor = self.state.love * 0.05  # 残留底——爱还在
            self.state.longing = max(floor, self.state.longing - longing_decay)

        for ch in FAST_CHANNELS:
            val = getattr(self.state, ch.value)
            hl = HALF_LIFE[ch]
            if hl is not None:
                # hyper 时负性情绪更难平复；hypo 时恢复也慢；正性不受影响。
                hl *= self.tolerance.decay_scale_for(ch.value)
            # 弹性衰减：偏离越远越快
            val = decay(val, hl, ctx.elapsed_minutes, baseline[ch])
            # 基线回弹（轻拉力）
            val = return_to_baseline(val, baseline[ch], ctx.elapsed_minutes)
            setattr(self.state, ch.value, val)

        if ctx.trace_data:
            ctx.trace_data.mark("after_decay", self.state)

    def _step_slow_channels(self, ctx: _TickContext) -> None:
        """Step 2: 慢通道缓慢生长。"""
        # L2: love 的正面事件增量只取“本 tick 新增”，不再用累计
        # positive_events。旧行为可用 legacy_slow_channels=True 复现。
        positive_now = 1 if (
            ctx.appraisal is not None
            and ctx.appraisal.goal_conduciveness > 0
            and ctx.appraisal.other_agency > 0
        ) else 0
        positive_input = (
            self.positive_events if self.legacy_slow_channels else positive_now
        )
        grow_slow_channels(self.state, self.events_today,
                           positive_input, ctx.elapsed_hours)
        ctx.positive_input = positive_input

        if ctx.trace_data:
            ctx.trace_data.mark("after_slow_channels", self.state)
            ctx.trace_data.slow_positive_input = positive_input

    def _step_appraisal(self, ctx: _TickContext) -> None:
        """Step 3: 记忆 / 感染先验 → 认知评估 → 门控 → 伤疤 → 氛围 → 写入。"""
        appraisal = ctx.appraisal
        raw: Dict[Channel, float] = {}
        gated: Dict[Channel, float] = {}
        gated_written: Dict[Channel, float] = {}
        scar_shift = 0.0
        scar_channels: List[str] = []
        atmosphere_applied = 0.0
        atmosphere_multipliers: Dict[str, float] = {}

        # L4: 记忆先验只在这里进入 Appraisal。无先验时不创建新对象、
        # 不写 trace 字段，保持旧路径逐值不变。
        if appraisal is not None and ctx.appraisal_bias:
            ctx.appraisal_before_memory = {
                name: float(getattr(appraisal, name))
                for name in APPRAISAL_LIMITS
            }
            biased = apply_appraisal_bias(appraisal, ctx.appraisal_bias)
            for name in APPRAISAL_LIMITS:
                delta = getattr(biased, name) - ctx.appraisal_before_memory[name]
                if abs(delta) > 1e-12:
                    ctx.memory_bias_applied[name] = delta
            appraisal = biased
            ctx.appraisal = appraisal
            if ctx.trace_data:
                ctx.trace_data.memory_bias_enabled = True
                ctx.trace_data.memory_bias_applied = dict(
                    ctx.memory_bias_applied
                )
                ctx.trace_data.appraisal_before_memory = dict(
                    ctx.appraisal_before_memory
                )

        # L7: 多角色感染先验。排在记忆先验之后、appraise() 之前；
        # 只改四维 Appraisal，不直接改 state / personality。无先验或
        # 无 Appraisal 时不创建新对象、不写 trace 字段。
        if appraisal is not None and ctx.contagion_bias:
            ctx.appraisal_before_contagion = {
                name: float(getattr(appraisal, name))
                for name in APPRAISAL_LIMITS
            }
            biased = apply_appraisal_bias(
                appraisal, ctx.contagion_bias, cap=MAX_CONTAGION_CAP,
            )
            for name in CONTAGION_DIMENSIONS:
                delta = (
                    getattr(biased, name)
                    - ctx.appraisal_before_contagion[name]
                )
                if abs(delta) > 1e-12:
                    ctx.contagion_applied[name] = delta
            appraisal = biased
            ctx.appraisal = appraisal
            ctx.contagion_enabled = True
            if ctx.trace_data:
                ctx.trace_data.contagion_enabled = True
                ctx.trace_data.contagion_applied = dict(
                    ctx.contagion_applied
                )
                ctx.trace_data.appraisal_before_contagion = dict(
                    ctx.appraisal_before_contagion
                )

        if appraisal is not None:
            raw = appraise(
                appraisal,
                guilt_self_agency_threshold=(
                    0.0 if self.legacy_slow_channels
                    else GUILT_SELF_AGENCY_THRESHOLD
                ),
            )
            gated = gate_appraisal(raw, self.state.trust, self.state.love,
                                   boundary_violation=ctx.boundary_violation)

            # 耐受窗调制：hyper 放大负性输入，hypo 降低反应强度。
            negative_gain = self.tolerance.negative_gain
            if negative_gain != 1.0:
                for ch in NEGATIVE_CHANNELS:
                    if ch in gated and gated[ch] > 0.001:
                        gated[ch] *= negative_gain

            # Step 3.5: 触发敏感化——旧伤疤放大负面情绪。
            # L3: 纯函数统一限制通道与 60% 上限。
            raw_scar_shift = self.scars.get_shift(appraisal)
            gated, effective_scar, scar_channels = apply_scar_amplification(
                gated, raw_scar_shift, appraisal.goal_conduciveness,
            )
            if scar_channels:
                scar_shift = effective_scar

            # 登记触发 / 正面反例
            tag = self.scars.detect(appraisal)
            if tag and appraisal.goal_conduciveness < 0:
                self.scars.register(tag, ctx.now)
            elif tag and appraisal.goal_conduciveness > 0:
                self.scars.register_positive(tag)

            # L3 corrected: 氛围在写入 state 之前调制 gated。
            # legacy 仍在写入之后调制，以复现旧行为。
            if not self.legacy_atmosphere:
                gated, atmosphere_multipliers = apply_atmosphere_modulation(
                    gated, ctx.atmosphere_before,
                )
                atmosphere_applied = ctx.atmosphere_before

            # 记录真正写入 state 的 gated（legacy 下这是晚调制前的版本）。
            gated_written = dict(gated)

            for ch in FAST_CHANNELS:
                if ch in gated and gated[ch] > 0.001:
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

        ctx.raw = raw
        ctx.gated = gated
        ctx.gated_written = gated_written
        ctx.scar_shift = scar_shift
        ctx.scar_channels = list(scar_channels)
        ctx.atmosphere_applied = atmosphere_applied
        ctx.atmosphere_multipliers = atmosphere_multipliers

        if ctx.trace_data:
            ctx.trace_data.mark("after_appraisal", self.state)

    def _step_after_appraisal(self, ctx: _TickContext) -> None:
        """Step 4: 氛围/越界处理；legacy 的晚调制也在这里。"""
        # 氛围目标使用的唤醒度：state 写入后、interactions 前捕获。
        # 这样 corrected 相对 legacy 的差异只来自 shock 密度本身。
        ctx.atmosphere_arousal = (self.state.fear + self.state.sadness) / 2.0

        if self.legacy_atmosphere:
            # ── pre-L3 兼容路径：先漂移，再晚调制 gated ──
            # 此时 shock 尚未检测，shock_channels 仍为空，密度恒为 0。
            recent_shock_intensity = len(ctx.shock_channels) / max(
                1, len(FAST_CHANNELS),
            )
            self._recent_shocks.append(recent_shock_intensity)
            if len(self._recent_shocks) > 5:
                self._recent_shocks.pop(0)

            ctx.shock_density = (
                sum(self._recent_shocks) / len(self._recent_shocks)
            )
            self.atmosphere = drift_atmosphere(
                self.atmosphere,
                shock_density=ctx.shock_density,
                arousal=ctx.atmosphere_arousal,
                boundary_violation=ctx.boundary_violation,
                clamp=False,
            )

            # 旧版晚调制：写入 state 后只影响输出/快照。
            if ctx.appraisal is not None:
                gated, atmosphere_multipliers = apply_atmosphere_modulation(
                    ctx.gated, self.atmosphere,
                )
                ctx.gated = gated
                ctx.atmosphere_multipliers = atmosphere_multipliers
                ctx.atmosphere_applied = self.atmosphere

            # 越界惩罚：低信任时被越界称呼 → trust 降
            if ctx.boundary_violation and self.state.trust < 0.5:
                self.state.trust = max(
                    0.01,
                    self.state.trust - (0.5 - self.state.trust) * 0.3,
                )

            if ctx.trace_data:
                ctx.trace_data.mark("after_atmosphere", self.state)
                if ctx.appraisal is not None and abs(self.atmosphere) > 1e-9:
                    ctx.trace_data.notes.append(
                        "atmosphere gate mutates gated after state write "
                        "(preserved L0 behavior)"
                    )
        else:
            # ── L3 corrected：越界惩罚在 interactions 前；氛围在 shock 后更新 ──
            if ctx.boundary_violation and self.state.trust < 0.5:
                self.state.trust = max(
                    0.01,
                    self.state.trust - (0.5 - self.state.trust) * 0.3,
                )

        if ctx.trace_data:
            ctx.trace_data.raw_appraisal = {
                k.value: v for k, v in ctx.raw.items()
            }
            ctx.trace_data.gated_appraisal = {
                k.value: v for k, v in ctx.gated.items()
            }
            ctx.trace_data.gated_written = {
                k.value: v for k, v in ctx.gated_written.items()
            }
            ctx.trace_data.scar_shift = ctx.scar_shift
            ctx.trace_data.scar_channels = list(ctx.scar_channels)
            ctx.trace_data.atmosphere_multipliers = dict(
                ctx.atmosphere_multipliers
            )

    def _step_interactions(self, ctx: _TickContext) -> None:
        """Step 5a: 交互矩阵。"""
        # L1.1: 默认只让“高于人格基线”的激活参与交互，避免基线本身
        # 在无事件时被当成情绪活动。legacy_interactions=True 保留旧行为。
        interaction_baseline = interaction_baselines(
            self.personality, self.state.love,
        ) if not self.legacy_slow_channels else ctx.baseline
        ctx.blends = apply_interactions(
            self.state,
            negative_scale=self.tolerance.interaction_scale,
            baseline=(
                None if self.legacy_interactions else interaction_baseline
            ),
            trust_ceiling=(
                None if self.legacy_slow_channels
                else trust_target(self.state.love)
            ),
        )

        if ctx.trace_data:
            ctx.trace_data.mark("after_interaction", self.state)
            ctx.trace_data.trust_target = trust_target(self.state.love)

    def _step_shock(self, ctx: _TickContext) -> None:
        """Step 5b: 对比度冲击检测，以及 corrected 的氛围漂移。"""
        for ch in FAST_CHANNELS:
            raw_delta = abs(self.state.delta(ch))
            # 指数冲击——但 clamp 输入防止溢出
            clamped = min(raw_delta, 3.0)  # Δ > 3.0 时感官已经饱和
            shock = math.exp(clamped * 2.0) - 1
            threshold = SHOCK_WEIGHT.get(ch, 0.3)
            if shock > threshold:
                ctx.shock_channels.append(ch.value)

        if self.legacy_atmosphere:
            if ctx.trace_data:
                ctx.trace_data.shock_channels = list(ctx.shock_channels)
                ctx.trace_data.blends = list(ctx.blends)
                # pre-L3：氛围在本轮 shock 检测前采样，密度恒为 0。
                ctx.trace_data.notes.append(
                    "shock density sampled before shock detection "
                    "(preserved L0 behavior)"
                )
        else:
            # L3 corrected：先检测 shock，再用本 tick 密度漂移氛围。
            # 新氛围作用于下一 tick 的 gated；避免同 tick 循环依赖。
            recent_shock_intensity = len(ctx.shock_channels) / max(
                1, len(FAST_CHANNELS),
            )
            self._recent_shocks.append(recent_shock_intensity)
            if len(self._recent_shocks) > 5:
                self._recent_shocks.pop(0)

            ctx.shock_density = (
                sum(self._recent_shocks) / len(self._recent_shocks)
            )
            self.atmosphere = drift_atmosphere(
                self.atmosphere,
                shock_density=ctx.shock_density,
                arousal=ctx.atmosphere_arousal,
                boundary_violation=ctx.boundary_violation,
                clamp=True,
            )

            if ctx.trace_data:
                ctx.trace_data.shock_channels = list(ctx.shock_channels)
                ctx.trace_data.blends = list(ctx.blends)
                ctx.trace_data.mark("after_atmosphere", self.state)
                ctx.trace_data.notes.append(
                    "L3 corrected: shock detected before atmosphere update; "
                    "density applies next tick"
                )
                if (ctx.appraisal is not None
                        and abs(ctx.atmosphere_applied) > 1e-9):
                    ctx.trace_data.notes.append(
                        "L3 corrected: atmosphere gate applied before state write"
                    )

    def _step_memory_and_saga(self, ctx: _TickContext) -> None:
        """Step 6/7: 旧版记忆存储、固化与 Saga 拉力。"""
        if ctx.appraisal is not None:
            arousal = self.state.surprise + len(ctx.shock_channels) * 0.5
            # Novelty gate: 30min 内同 arousal ±0.15 且同 relevance ±0.2 → 跳过
            recent = (
                self.memory.recent_items(ctx.now, 30)
                if hasattr(self.memory, "recent_items")
                else [
                    m for m in getattr(self.memory, "short_term", [])
                    if ctx.now - m.timestamp < 30 * 60
                ]
            )
            is_novel = all(
                abs(m.arousal - arousal) > 0.15 or
                abs(m.relevance - ctx.appraisal.goal_relevance) > 0.2
                for m in recent
            )
            if is_novel or len(recent) == 0:
                mem = MemoryItem(
                    content=(
                        f"gc={ctx.appraisal.goal_conduciveness:.1f} "
                        f"gr={ctx.appraisal.goal_relevance:.1f}"
                    ),
                    timestamp=ctx.now,
                    emotion_snap=self.state.to_dict(),
                    arousal=arousal,
                    relevance=ctx.appraisal.goal_relevance,
                )
                self.memory.store(mem)

        # 30分钟固化窗口检查
        self.memory.consolidate()
        # 短期记忆清理
        self.memory.decay_short_term()

        # Step 7: Saga 效应（每累计 24h 应用一次长期记忆基线拉力）
        saga_elapsed = (ctx.now - self._last_saga) / 3600.0  # 小时
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
            self._last_saga = ctx.now

    def _step_meta_and_tolerance(self, ctx: _TickContext) -> None:
        """Step 8: meta 预计算 → 耐受窗刷新 → trace 收尾。"""
        # L5: 可选的 meta 负荷必须在耐受窗更新前算好。只消费上一 tick
        # 的 zone/overload，避免 meta ↔ tolerance 在同一 tick 内互相反馈。
        meta_state = None
        meta_load_bias = 0.0
        if (
            self.meta_emotion_enabled
            and self.tolerance_meta_load_enabled
            and not self.legacy_tolerance
            and self.tolerance.capacity_model == CAPACITY_MODEL_MULTI
        ):
            meta_state = evaluate_meta_emotion(
                state=self.state,
                baseline=self.personality.baseline(),
                personality=self.personality,
                appraisal=ctx.appraisal,
                atmosphere=self.atmosphere,
                tolerance_zone=self.tolerance.zone,
                overload_ratio=self.tolerance.overload_ratio,
                scar_pressure=ctx.scar_shift,
                scar_channels=tuple(ctx.scar_channels),
                blends=tuple(ctx.blends),
                profile=self.meta_emotion_profile,
            )
            meta_load_bias = meta_state.regulation_bias.tolerance_load_bias
        ctx.meta_state = meta_state
        ctx.meta_load_bias = meta_load_bias

        # Step 8: 刷新耐受窗。放在通道与氛围更新之后，
        # 让本 tick 的过载在下一 tick 才影响增益（带两 tick 滞回）。
        self._update_tolerance(
            shock_channels=tuple(ctx.shock_channels),
            boundary_violation=ctx.boundary_violation,
            elapsed_hours=ctx.elapsed_hours,
            meta_load_bias=meta_load_bias,
        )

        # 更新计时器
        self.state._last_update = ctx.now

        if ctx.trace_data:
            trace = ctx.trace_data
            trace.mark("final", self.state)
            trace.atmosphere = self.atmosphere
            trace.atmosphere_after = self.atmosphere
            trace.atmosphere_applied = ctx.atmosphere_applied
            trace.atmosphere_shock_density = ctx.shock_density
            trace.atmosphere_arousal = ctx.atmosphere_arousal
            trace.tolerance_zone = self.tolerance.zone
            # L3.5: 元情绪只读最终状态与上下文，只写 trace。
            # 放在所有 state / atmosphere / tolerance 更新之后，确保不反向
            # 影响本 tick 或后续曲线。
            if self.meta_emotion_enabled:
                if meta_state is None:
                    meta_state = evaluate_meta_emotion(
                        state=self.state,
                        baseline=self.personality.baseline(),
                        personality=self.personality,
                        appraisal=ctx.appraisal,
                        atmosphere=self.atmosphere,
                        tolerance_zone=self.tolerance.zone,
                        overload_ratio=self.tolerance.overload_ratio,
                        scar_pressure=trace.scar_shift,
                        scar_channels=tuple(trace.scar_channels),
                        blends=tuple(ctx.blends),
                        profile=self.meta_emotion_profile,
                    )
                meta_payload = meta_state.to_trace_dict()
                trace.meta_enabled = True
                trace.meta_appraisal = meta_payload["meta_appraisal"]
                trace.meta_emotions = meta_payload["meta_emotions"]
                trace.regulation_bias = meta_payload["regulation_bias"]

    def _build_tick_result(self, ctx: _TickContext) -> Dict:
        """把 tick 的最终状态与 trace 组装成返回值。"""
        felt = self.state.felt_all()
        mem_stats = self.memory.stats()
        result = {
            "state":          self.state.to_dict(),
            "felt":           felt,  # log压缩后的感知值
            "blends":         ctx.blends,
            "shock_channels": ctx.shock_channels,
            "raw_appraisal":  {
                k.value: round(v, 3) for k, v in ctx.raw.items()
            },
            "gated_appraisal": {
                k.value: round(v, 3) for k, v in ctx.gated.items()
            },
            "trust":          round(self.state.trust, 3),
            "love":           round(self.state.love, 3),
            "memory":         mem_stats,
            "atmosphere":     round(self.atmosphere, 3),
            "tolerance":      self.tolerance.to_dict(),
        }
        if ctx.appraisal_before_memory:
            result["memory_bias"] = {
                key: round(value, 4)
                for key, value in ctx.memory_bias_applied.items()
            }
        if ctx.contagion_enabled:
            result["contagion_bias"] = {
                key: round(value, 4)
                for key, value in ctx.contagion_applied.items()
            }
        if ctx.trace_data is not None:
            result["trace"] = ctx.trace_data.to_dict()
        return result


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
            p.threshold_shift = min(
                MAX_SCAR_SHIFT, excess * 0.08,
            )  # 单次门槛降 8%, 最大降 60%

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
            return clamp_scar_shift(self.patterns[tag].threshold_shift)
        return 0.0

    def all_scars(self) -> Dict[str, float]:
        """返回所有旧伤疤及其敏感化程度。"""
        return {tag: p.threshold_shift for tag, p in self.patterns.items()
                if p.threshold_shift > 0.01}


# ══════════════════════════════════════════════════════
# v0.5 SQLite 持久化
# ══════════════════════════════════════════════════════

DB_PATH = "emotion.db"
# 快照 payload schema：v1 = 无版本字段的旧 payload / 直接通道字典；
# v2 = 当前完整 payload。每次改变 payload 形状时递增，并在
# _migrate_snapshot_payload() 里补一段迁移。
SNAPSHOT_VERSION = 2


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


def _migrate_snapshot_payload(data: Any) -> Optional[Dict[str, Any]]:
    """把旧快照 payload 统一到当前 schema。

    返回 None 表示无法安全读取：非 dict、版本号非法、或来自更新版本。
    旧格式（v0.5-v0.6 直接把通道字典放在 state_json）会被包装成 v1。
    """
    if not isinstance(data, dict):
        return None

    # v0.5-v0.6：state_json 直接是通道字典，没有外层 payload。
    if "state" not in data and any(ch.value in data for ch in Channel):
        data = {"state": data}

    version = data.get("schema_version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        return None
    if version < 1 or version > SNAPSHOT_VERSION:
        return None

    payload = dict(data)
    # v1 -> v2：只增加 schema_version 标记，数据形状不变。
    # 未来改变字段时在这里按 version 做重命名/补默认值。
    payload["schema_version"] = SNAPSHOT_VERSION
    return payload


def save_snapshot(engine: "EmotionEngine", path: str = DB_PATH,
                  now: Optional[float] = None) -> bool:
    """保存完整引擎状态——断线恢复用。

    旧版只存通道值与 atmosphere，恢复后耐受窗、事件计数、Saga 计时
    和最近 shock 全部丢失。这里统一存成一个 JSON payload，同时保留
    atmosphere 列以兼容旧读取逻辑。
    """
    saved_at = time.time() if now is None else now
    # 快照可能发生在引擎首次 tick 之前；确保表结构存在。
    init_db(path).close()
    db = _get_db(path)
    try:
        payload = {
            "schema_version": SNAPSHOT_VERSION,
            "state": engine.state.to_dict(),
            "tolerance": engine.tolerance.to_dict(),
            "atmosphere": engine.atmosphere,
            "events_today": engine.events_today,
            "positive_events": engine.positive_events,
            "last_saga": engine._last_saga,
            "recent_shocks": list(engine._recent_shocks),
            "legacy_interactions": engine.legacy_interactions,
            "legacy_slow_channels": engine.legacy_slow_channels,
            "legacy_atmosphere": engine.legacy_atmosphere,
            "legacy_tolerance": engine.legacy_tolerance,
        }
        db.execute(
            "INSERT INTO snapshots (state_json, atmosphere, saved_at) VALUES (?,?,?)",
            (json.dumps(payload, ensure_ascii=False), engine.atmosphere, saved_at))
        for tag, pattern in engine.scars.patterns.items():
            db.execute(
                "INSERT OR REPLACE INTO scars VALUES (?,?,?,?,?)",
                (tag, pattern.trigger_count, pattern.threshold_shift,
                 pattern.positive_counter, pattern.last_triggered))
        db.commit()
        return True
    finally:
        db.close()


def load_snapshot(engine: "EmotionEngine", path: str = DB_PATH) -> bool:
    """从上次快照恢复。返回 True 如果成功。

    兼容 v0.5-v0.6 的旧快照：那种 state_json 直接是通道字典，
    没有外层 payload，也没有 tolerance / 计数器。
    """
    if not os.path.exists(path):
        return False
    # 旧库可能只有 memories/scars、没有 snapshots 表。
    init_db(path).close()
    db = _get_db(path)
    try:
        row = db.execute(
            "SELECT state_json, atmosphere, saved_at FROM snapshots "
            "ORDER BY saved_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return False

        data = _migrate_snapshot_payload(json.loads(row[0]))
        if data is None:
            return False
        state_data = data.get("state", data)
        if not isinstance(state_data, dict):
            return False

        for ch in Channel:
            if ch.value in state_data:
                setattr(engine.state, ch.value, float(state_data[ch.value]))

        snapshot_atmosphere = data.get("atmosphere", row[1])

        # L5: 先恢复 legacy flag，再用“当前配置 + 必要 scalar 化”重建状态。
        # tolerance_config 是代码级配置，不写入快照；续跑时需重新提供。
        if "legacy_tolerance" in data:
            engine.legacy_tolerance = bool(data.get("legacy_tolerance"))
        tolerance_config = engine.tolerance_config or engine.tolerance.config
        if tolerance_config.capacity_model not in VALID_CAPACITY_MODELS:
            tolerance_config = replace(
                tolerance_config, capacity_model=CAPACITY_MODEL_MULTI,
            )
        if engine.legacy_tolerance:
            tolerance_config = replace(
                tolerance_config, capacity_model=CAPACITY_MODEL_SCALAR,
            )
        engine.tolerance_config = tolerance_config
        if "tolerance" in data:
            engine.tolerance = ToleranceState.from_dict(
                data.get("tolerance"), config=tolerance_config,
            )
            if engine.legacy_tolerance:
                engine.tolerance.capacity_model = CAPACITY_MODEL_SCALAR
        else:
            engine.tolerance.config = tolerance_config
            engine.tolerance.capacity_model = tolerance_config.capacity_model
        if "events_today" in data:
            engine.events_today = int(data.get("events_today", 0))
        if "positive_events" in data:
            engine.positive_events = int(data.get("positive_events", 0))
        if "last_saga" in data:
            engine._last_saga = float(data.get("last_saga", engine._last_saga))
        if isinstance(data.get("recent_shocks"), list):
            engine._recent_shocks = [
                float(value) for value in data["recent_shocks"][-5:]
            ]
        if "legacy_interactions" in data:
            engine.legacy_interactions = bool(data.get("legacy_interactions"))
        if "legacy_slow_channels" in data:
            engine.legacy_slow_channels = bool(data.get("legacy_slow_channels"))
        if "legacy_atmosphere" in data:
            engine.legacy_atmosphere = bool(data.get("legacy_atmosphere"))

        # 先恢复 flag，再决定是否 clamp，避免 legacy 模式被悄悄改写。
        if snapshot_atmosphere is not None:
            engine.atmosphere = float(snapshot_atmosphere)
        if not engine.legacy_atmosphere:
            engine.atmosphere = clamp_atmosphere(engine.atmosphere)

        # 以快照时间作为计时起点，避免恢复后把停机时间误算成在线时间。
        if row[2] is not None:
            engine.state._last_update = float(row[2])

        for row_s in db.execute("SELECT * FROM scars"):
            tag, cnt, shift, pos, last = row_s
            engine.scars.patterns[tag] = SensitizationPattern(
                tag=tag, trigger_count=cnt, threshold_shift=shift,
                positive_counter=pos, last_triggered=last,
            )
        return True
    finally:
        db.close()


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
        cursor = self.db.execute(
            "INSERT INTO memories (content,tier,arousal,relevance,emotion_snap,timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (item.content, item.tier, item.arousal, item.relevance,
             json.dumps(item.emotion_snap), item.timestamp))
        # consolidate() 依赖 _db_id 更新 tier；之前没写回，
        # 导致 WHERE id=NULL 永远匹配不到，长期记忆晋升实际失效。
        item._db_id = int(cursor.lastrowid)
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


# 表达层已移到 expression.py；名字在本文件顶部 import，
# 旧路径 `from engine import _build_llm_prompt` 等仍然可用。


def respond_llm(engine: "EmotionEngine", user_input: str,
                character_id: str = "default",
                character_name: str = "Character",
                character_desc: str = "A person with complex emotions.",
                api_key: Optional[str] = None,
                base_url: str = "https://api.deepseek.com/v1",
                model: str = "deepseek-chat",
                fallback_key: Optional[str] = None,
                fallback_url: str = "https://api.moonshot.cn/v1",
                fallback_model: str = "moonshot-v1-8k",
                memory_context: Optional[str] = None,
                relationship_memory: Any = None,
                user_id: str = "default",
                record_turn: bool = True,
                appraisal: Optional["Appraisal"] = None,
                memory_appraisal_bias: Optional[Dict[str, float]] = None,
                use_jev: bool = False,
                jev: Any = None,
                auto_boundary: bool = False,
                boundary_violation: Optional[bool] = None,
                trace: bool = False,
                contagion_bias: Optional[Dict[str, float]] = None,
                ) -> Dict:
    """
    LLM 增强回应——引擎算情绪，LLM 写表达。
    主 API 失败 → fallback API → 都失败则回退到本地模板。

    relationship_memory: 可选的 memory.MemoryService。
      传入后，用关系域记忆构建 prompt，并在回复后尝试抽取记忆。
    memory_context: 外部已构建好的记忆区块；优先级高于自动召回。
    appraisal: 可选六维评估；L4 记忆先验只通过它进入引擎。
    memory_appraisal_bias: 外部已有的六维记忆先验；不传则按关系域自动召回。
    use_jev: 没有 appraisal 时，用 JEV 从 user_input 生成六维评估。
      默认关闭，保持旧调用行为不变。
    jev: 可注入的 jev.JEV 实例（便于测试 / 自定义缓存）。
    auto_boundary: 用 Noul 检测文本边界侵犯；显式 boundary_violation 优先。
    boundary_violation: 调用方已经判定好的边界侵犯；None 表示未判定。
    trace: 是否在返回值里附带 StepTrace（用于回放/仪表盘）。
    contagion_bias: L7 多角色感染先验；原样传给 respond()，默认关闭。
    """
    jev_result = None
    jev_error = None
    if appraisal is None and use_jev:
        try:
            if jev is None:
                from jev import analyze as analyze_text
                jev_result = analyze_text(user_input)
            else:
                jev_result = jev.analyze(user_input)
            appraisal = jev_result.appraisal
        except Exception as exc:  # JEV 不可用时全链路仍可运行
            jev_error = str(exc)

    boundary_signal = None
    boundary_error = None
    if boundary_violation is None:
        boundary_violation = False
        if auto_boundary:
            try:
                from noul import detect as detect_boundary
                boundary_signal = detect_boundary(
                    user_input, trust=engine.state.trust,
                )
                boundary_violation = boundary_signal.violated
            except Exception as exc:
                boundary_error = str(exc)

    # L4：先取有界记忆先验，再跑情绪引擎。没有 appraisal 时先验不会
    # 直接拉状态，只保留在返回值里供上游 JEV / 调试使用。
    prior_error = None
    if relationship_memory is not None and memory_appraisal_bias is None:
        try:
            memory_appraisal_bias = relationship_memory.appraisal_prior(
                character_id, user_id,
            )
        except Exception as exc:
            prior_error = str(exc)

    # 先跑情绪引擎
    local = respond(
        engine, user_input, appraisal=appraisal,
        character_id=character_id,
        memory_appraisal_bias=memory_appraisal_bias,
        boundary_violation=boundary_violation,
        trace=trace,
        contagion_bias=contagion_bias,
    )
    if prior_error:
        local["_memory_prior_error"] = prior_error
    if jev_result is not None:
        local["_jev"] = jev_result.to_dict()
    if jev_error:
        local["_jev_error"] = jev_error
    if boundary_signal is not None:
        local["_boundary"] = boundary_signal.to_dict()
    if boundary_error:
        local["_boundary_error"] = boundary_error

    # 旧版全局记忆（没有关系域记忆服务时使用）
    recent = []
    if relationship_memory is None:
        recent = engine.memory.recall(
            engine.state, len(local.get("shock_channels", []))
        )

    # 新版关系域记忆：按 character_id × user_id 隔离召回
    relational_context = memory_context or ""
    if relationship_memory is not None and not relational_context:
        try:
            relational_context = relationship_memory.build_context(
                character_id=character_id,
                user_id=user_id,
                query=user_input,
            )
        except Exception as exc:
            relational_context = ""
            local["_memory_context_error"] = str(exc)

    # 构建 prompt
    prompt = _build_llm_prompt(engine, user_input, character_name,
                               character_desc, recent,
                               memory_context=relational_context)

    # 按顺序尝试 API：主 → fallback。网络细节在 llm_client。
    llm_text, llm_backend = call_llm(
        prompt,
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_key=fallback_key,
        fallback_url=fallback_url,
        fallback_model=fallback_model,
    )
    if llm_backend:
        local["_llm_backend"] = llm_backend

    local["_llm_used"] = llm_text is not None
    if llm_text:
        local["utterance"] = llm_text
    # 否则保持 local 的模板 utterance

    # 回复后抽取记忆（当前同步执行；生产环境可挪到后台队列）。
    # 抽取器只写高置信事实/边界/承诺，不会把整段对话塞进库。
    if record_turn and relationship_memory is not None:
        try:
            relationship_memory.remember_turn(
                character_id=character_id,
                user_id=user_id,
                user_text=user_input,
                assistant_text=local.get("utterance", ""),
                appraisal=appraisal,
                emotion_snap=_emotion_snapshot(engine.state),
            )
        except Exception as exc:
            local["_memory_error"] = str(exc)

    return local

def respond(engine: "EmotionEngine", event_type: str,
            appraisal: Optional["Appraisal"] = None,
            character_id: str = "default",
            memory_appraisal_bias: Optional[Dict[str, float]] = None,
            boundary_violation: bool = False,
            trace: bool = False,
            contagion_bias: Optional[Dict[str, float]] = None) -> Dict:
    """
    纯本地计算。零 API 调用。
    character_id: 选角色专属模板；hyper/hypo 时 zone 优先于主导情绪。
    memory_appraisal_bias: L4 记忆先验；无 appraisal 时不会拉状态。
    boundary_violation: 文本/上游已判定的边界侵犯。
    trace: 是否在返回值里附带 StepTrace。
    contagion_bias: L7 多角色感染先验；无 appraisal 时不会拉状态。
    返回: 表情参数 + 行为倾向 + 简短回应 + 是否需要 LLM。
    """
    tick_kwargs: Dict[str, Any] = {}
    if memory_appraisal_bias:
        tick_kwargs["appraisal_bias"] = memory_appraisal_bias
    if contagion_bias:
        tick_kwargs["contagion_bias"] = contagion_bias
    result = engine.tick(
        appraisal,
        boundary_violation=boundary_violation,
        trace=trace,
        **tick_kwargs,
    )
    s = result["state"]
    atmos = result.get("atmosphere", 0)

    dom = _dominant_channel(engine.state)
    tolerance = result.get("tolerance", {})
    zone = tolerance.get("zone", "regulated")

    # 角色专属模板：超载时 zone 优先于主导情绪。
    utterance = _select_local_utterance(character_id, dom, zone)

    # 是否触发 LLM（只在异常状态时）
    shock_count = len(result.get("shock_channels", []))
    needs_llm = (engine.state.surprise > 0.8 or shock_count > 4 or
                 len(result.get("blends", [])) > 2 or
                 zone in (ZONE_HYPER, ZONE_HYPO))

    response = {
        # Live2D 参数映射（L5.1 起与 zone 一致）
        "expression":      _expression_params(engine.state, zone),
        # 行为
        "action":          _action_tendency(
            engine.state, atmos, zone,
        ),
        "atmosphere":      round(atmos, 3),
        "tolerance":       tolerance,
        # 简短回应（模板，非 LLM）
        "utterance":       utterance,
        # 是否建议调 LLM
        "needs_llm":       needs_llm,
        # 底层情绪数据（调试用）
        "_state":          s,
        "_dominant":       dom,
        "shock_channels":  result.get("shock_channels", []),
        "blends":          result.get("blends", []),
    }
    if "memory_bias" in result:
        response["memory_bias"] = result["memory_bias"]
    if "contagion_bias" in result:
        response["contagion_bias"] = result["contagion_bias"]
    if trace and "trace" in result:
        response["trace"] = result["trace"]
    return response


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

    init_db("test_emotion.db").close()
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
        print("  State preserved across restart: OK")
    else:
        print("  Restore failed")

    # 清理：两个引擎的 SQLite 连接都要关，否则 Windows 无法删除文件。
    engine.memory.db.close()
    engine2.memory.db.close()
    if os.path.exists("test_emotion.db"):
        os.remove("test_emotion.db")
