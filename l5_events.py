"""L5 耐受校准 golden set 的冻结场景。

这里只放纯数据，不导入 engine / meta_emotion。所有状态是完整的
10 通道字典，baseline 固定为 BASE；scenario.config 只覆盖
ToleranceConfig 的字段，保证 legacy 场景可以在 pre-L5 代码上运行。

场景覆盖：
  - 平静 / 愤怒过载 / 退缩型过载；
  - 两 tick 滞回、4h 恢复；
  - trust 缺口 deadband；
  - 紧绷 / 放松氛围下的域容量差异；
  - 四类伤疤各自的定向压缩；
  - 多伤疤同域 max；
  - meta load bias 的附加、封顶与不累加；
  - 组合压力、容量上下 clamp、长序列稳定性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

# 与 test_tolerance.py 的 BASE 保持一致，便于直接对照旧标量行为。
BASE: Dict[str, float] = {
    "joy": 0.30,
    "sadness": 0.10,
    "anger": 0.05,
    "fear": 0.05,
    "love": 0.20,
    "disgust": 0.00,
    "surprise": 0.00,
    "trust": 0.25,
    "longing": 0.00,
    "guilt": 0.00,
}


def _state(**overrides: float) -> Dict[str, float]:
    state = dict(BASE)
    state.update(overrides)
    return state


CALM = _state()

# 战/逃型过载：愤怒 + 恐惧 + 厌恶 + 越界。
STRESS = _state(
    joy=0.05,
    sadness=0.35,
    anger=0.95,
    fear=0.75,
    disgust=0.60,
    trust=0.10,
    love=0.18,
)

# 退缩型过载：悲伤 + 内疚 + 思念占优。
WITHDRAW = _state(
    joy=0.00,
    sadness=1.20,
    anger=0.05,
    fear=0.70,
    disgust=0.00,
    trust=0.02,
    love=0.18,
    longing=1.20,
    guilt=1.00,
)

# 组合风暴：高负荷 + 多伤疤 + 高神经质 + 紧绷氛围。
STORM = _state(
    joy=0.00,
    sadness=0.95,
    anger=1.00,
    fear=0.95,
    disgust=0.80,
    trust=0.03,
    love=0.12,
    longing=0.80,
    guilt=0.75,
)


@dataclass(frozen=True)
class L5Step:
    """一个耐受窗 update 的完整输入。"""

    key: str
    state: Mapping[str, float]
    trust_target_value: Optional[float] = 0.24
    neuroticism: float = 0.5
    atmosphere: float = -0.3
    shock_channels: Tuple[str, ...] = ()
    boundary_violation: bool = False
    scar_pressure: float = 0.0
    scar_pressure_by_tag: Optional[Mapping[str, float]] = None
    meta_load_bias: float = 0.0
    elapsed_hours: float = 0.0


@dataclass(frozen=True)
class L5Scenario:
    """一个独立场景；config 只覆盖 ToleranceConfig 字段。"""

    key: str
    label: str
    steps: Tuple[L5Step, ...]
    config: Mapping[str, float] = field(default_factory=dict)
    note: str = ""


def _calm_steps(prefix: str, count: int,
                elapsed_hours: float = 0.0) -> Tuple[L5Step, ...]:
    return tuple(
        L5Step(
            key=f"{prefix}.{index}",
            state=CALM,
            elapsed_hours=elapsed_hours,
        )
        for index in range(count)
    )


SCENARIOS: Tuple[L5Scenario, ...] = (
    L5Scenario(
        key="l5.calm_baseline",
        label="平静基线",
        steps=_calm_steps("l5.calm_baseline", 3),
        note="load≈0，regulated，四域容量都应输出。",
    ),
    L5Scenario(
        key="l5.anger_hyper",
        label="战逃型过载",
        steps=(
            L5Step(
                key="l5.anger_hyper.0",
                state=STRESS,
                trust_target_value=0.50,
                neuroticism=0.70,
                atmosphere=0.35,
                shock_channels=("anger", "fear", "disgust"),
                boundary_violation=True,
            ),
            L5Step(
                key="l5.anger_hyper.1",
                state=STRESS,
                trust_target_value=0.50,
                neuroticism=0.70,
                atmosphere=0.35,
                shock_channels=("anger", "fear", "disgust"),
                boundary_violation=True,
            ),
        ),
        note="第 1 tick 只 pending，第 2 tick 切 hyper。",
    ),
    L5Scenario(
        key="l5.withdrawal_hypo",
        label="退缩型过载",
        steps=(
            L5Step(
                key="l5.withdrawal_hypo.0",
                state=WITHDRAW,
                trust_target_value=0.41,
                neuroticism=0.50,
                atmosphere=-0.10,
                shock_channels=("sadness", "fear", "guilt", "surprise"),
            ),
            L5Step(
                key="l5.withdrawal_hypo.1",
                state=WITHDRAW,
                trust_target_value=0.41,
                neuroticism=0.50,
                atmosphere=-0.10,
                shock_channels=("sadness", "fear", "guilt", "surprise"),
            ),
        ),
        note="退缩分高于战逃分，过载后进入 hypo 而不是 hyper。",
    ),
    L5Scenario(
        key="l5.recovery_hysteresis",
        label="过载后两 tick 恢复",
        steps=(
            L5Step(
                key="l5.recovery_hysteresis.0",
                state=STRESS,
                trust_target_value=0.50,
                neuroticism=0.70,
                atmosphere=0.35,
                shock_channels=("anger", "fear", "disgust"),
                boundary_violation=True,
            ),
            L5Step(
                key="l5.recovery_hysteresis.1",
                state=STRESS,
                trust_target_value=0.50,
                neuroticism=0.70,
                atmosphere=0.35,
                shock_channels=("anger", "fear", "disgust"),
                boundary_violation=True,
            ),
            L5Step(
                key="l5.recovery_hysteresis.2",
                state=CALM,
                elapsed_hours=4.0,
            ),
            L5Step(
                key="l5.recovery_hysteresis.3",
                state=CALM,
                elapsed_hours=4.0,
            ),
        ),
        note="4h 衰减后仍需两 tick 才从 hyper 回到 regulated。",
    ),
    L5Scenario(
        key="l5.trust_deadband",
        label="trust 缺口 deadband",
        steps=(
            L5Step(
                key="l5.trust_deadband.0",
                state=_state(trust=0.50),
                trust_target_value=0.70,
            ),
            L5Step(
                key="l5.trust_deadband.1",
                state=_state(trust=0.30),
                trust_target_value=0.70,
            ),
            L5Step(
                key="l5.trust_deadband.2",
                state=_state(trust=0.05),
                trust_target_value=0.70,
            ),
        ),
        note="缺口 0.20 不加载；超过 0.25 后才按比例计入。",
    ),
    L5Scenario(
        key="l5.tense_capacity",
        label="紧绷氛围下的容量",
        steps=(
            L5Step(
                key="l5.tense_capacity.0",
                state=CALM,
                neuroticism=0.85,
                atmosphere=0.60,
            ),
        ),
        note="高神经质 + 紧绷氛围应压缩 fear/criticism 更多。",
    ),
    L5Scenario(
        key="l5.relaxed_capacity",
        label="放松氛围下的容量",
        steps=(
            L5Step(
                key="l5.relaxed_capacity.0",
                state=CALM,
                neuroticism=0.85,
                atmosphere=-0.60,
            ),
        ),
        note="与 tense_capacity 对照，放松氛围略微扩展容量。",
    ),
    L5Scenario(
        key="l5.scar_event_types",
        label="四类伤疤的定向压缩",
        steps=(
            L5Step(
                key="l5.scar_event_types.0",
                state=CALM,
                scar_pressure=0.60,
                scar_pressure_by_tag={"social_hurt": 0.60},
            ),
            L5Step(
                key="l5.scar_event_types.1",
                state=CALM,
                scar_pressure=0.60,
                scar_pressure_by_tag={"criticism": 0.60},
            ),
            L5Step(
                key="l5.scar_event_types.2",
                state=CALM,
                scar_pressure=0.60,
                scar_pressure_by_tag={"cold_shoulder": 0.60},
            ),
            L5Step(
                key="l5.scar_event_types.3",
                state=CALM,
                scar_pressure=0.60,
                scar_pressure_by_tag={"self_blame": 0.60},
            ),
        ),
        note="social_hurt 压 anger/fear/criticism；self_blame 只压 intimacy。",
    ),
    L5Scenario(
        key="l5.scar_multi_tag",
        label="多伤疤同域取 max",
        steps=(
            L5Step(
                key="l5.scar_multi_tag.0",
                state=CALM,
                scar_pressure=0.60,
                scar_pressure_by_tag={
                    "social_hurt": 0.40,
                    "criticism": 0.60,
                    "cold_shoulder": 0.30,
                    "self_blame": 0.20,
                },
            ),
        ),
        note="同域内取最大，不把多个伤疤相加。",
    ),
    L5Scenario(
        key="l5.meta_bias_additive",
        label="meta load 附加与封顶",
        steps=(
            L5Step(
                key="l5.meta_bias_additive.0",
                state=CALM,
                meta_load_bias=0.00,
            ),
            L5Step(
                key="l5.meta_bias_additive.1",
                state=CALM,
                meta_load_bias=0.05,
            ),
            L5Step(
                key="l5.meta_bias_additive.2",
                state=CALM,
                meta_load_bias=0.20,
            ),
            L5Step(
                key="l5.meta_bias_additive.3",
                state=CALM,
                meta_load_bias=0.50,
            ),
            L5Step(
                key="l5.meta_bias_additive.4",
                state=CALM,
                meta_load_bias=0.00,
                elapsed_hours=4.0,
            ),
        ),
        note="bias 只加在 instant load，硬上限 0.20；最后一步按 4h 衰减。",
    ),
    L5Scenario(
        key="l5.meta_bias_with_scar",
        label="meta load 与伤疤不重复计",
        steps=(
            L5Step(
                key="l5.meta_bias_with_scar.0",
                state=CALM,
                scar_pressure=0.60,
                scar_pressure_by_tag={"criticism": 0.60},
                meta_load_bias=0.00,
            ),
            L5Step(
                key="l5.meta_bias_with_scar.1",
                state=CALM,
                scar_pressure=0.60,
                scar_pressure_by_tag={"criticism": 0.60},
                meta_load_bias=0.20,
            ),
            L5Step(
                key="l5.meta_bias_with_scar.2",
                state=CALM,
                scar_pressure=0.60,
                scar_pressure_by_tag={"criticism": 0.60},
                meta_load_bias=0.00,
                elapsed_hours=4.0,
            ),
        ),
        note="三步容量/域容量必须一致；只有 load 随 bias 变化。",
    ),
    L5Scenario(
        key="l5.combined_stress",
        label="组合压力",
        steps=(
            L5Step(
                key="l5.combined_stress.0",
                state=STORM,
                trust_target_value=0.55,
                neuroticism=0.85,
                atmosphere=0.60,
                shock_channels=(
                    "sadness", "anger", "fear", "disgust", "guilt",
                ),
                boundary_violation=True,
                scar_pressure=0.60,
                scar_pressure_by_tag={
                    "social_hurt": 0.50,
                    "criticism": 0.60,
                    "cold_shoulder": 0.40,
                    "self_blame": 0.30,
                },
                meta_load_bias=0.20,
            ),
            L5Step(
                key="l5.combined_stress.1",
                state=STORM,
                trust_target_value=0.55,
                neuroticism=0.85,
                atmosphere=0.60,
                shock_channels=(
                    "sadness", "anger", "fear", "disgust", "guilt",
                ),
                boundary_violation=True,
                scar_pressure=0.60,
                scar_pressure_by_tag={
                    "social_hurt": 0.50,
                    "criticism": 0.60,
                    "cold_shoulder": 0.40,
                    "self_blame": 0.30,
                },
                meta_load_bias=0.20,
            ),
        ),
        note="高负荷 + 多伤疤 + 高神经质 + 紧绷 + 越界 + meta bias。",
    ),
    L5Scenario(
        key="l5.capacity_floor",
        label="容量下界",
        steps=(
            L5Step(
                key="l5.capacity_floor.0",
                state=_state(trust=0.0, love=0.0),
                neuroticism=1.0,
                atmosphere=1.0,
                scar_pressure=1.0,
                scar_pressure_by_tag={
                    "social_hurt": 1.0,
                    "criticism": 1.0,
                    "cold_shoulder": 1.0,
                    "self_blame": 1.0,
                },
            ),
        ),
        config={"base_capacity": 0.0},
        note="极端低容量应被 clamp 到 min_capacity。",
    ),
    L5Scenario(
        key="l5.capacity_ceiling",
        label="容量上界",
        steps=(
            L5Step(
                key="l5.capacity_ceiling.0",
                state=_state(trust=1.0, love=1.0),
                neuroticism=0.0,
                atmosphere=-1.0,
            ),
        ),
        config={"base_capacity": 2.0},
        note="极端高容量应被 clamp 到 max_capacity。",
    ),
    L5Scenario(
        key="l5.long_alternating",
        label="长序列风暴/平静交替",
        steps=(
            L5Step(
                key="l5.long_alternating.0",
                state=STRESS,
                trust_target_value=0.50,
                neuroticism=0.70,
                atmosphere=0.35,
                shock_channels=("anger", "fear", "disgust"),
                boundary_violation=True,
            ),
            L5Step(
                key="l5.long_alternating.1",
                state=STRESS,
                trust_target_value=0.50,
                neuroticism=0.70,
                atmosphere=0.35,
                shock_channels=("anger", "fear", "disgust"),
                boundary_violation=True,
            ),
            L5Step(
                key="l5.long_alternating.2",
                state=CALM,
                elapsed_hours=4.0,
            ),
            L5Step(
                key="l5.long_alternating.3",
                state=CALM,
                elapsed_hours=4.0,
            ),
            L5Step(
                key="l5.long_alternating.4",
                state=STRESS,
                trust_target_value=0.50,
                neuroticism=0.70,
                atmosphere=0.35,
                shock_channels=("anger", "fear", "disgust"),
                boundary_violation=True,
            ),
            L5Step(
                key="l5.long_alternating.5",
                state=STRESS,
                trust_target_value=0.50,
                neuroticism=0.70,
                atmosphere=0.35,
                shock_channels=("anger", "fear", "disgust"),
                boundary_violation=True,
            ),
            L5Step(
                key="l5.long_alternating.6",
                state=CALM,
                elapsed_hours=4.0,
            ),
            L5Step(
                key="l5.long_alternating.7",
                state=CALM,
                elapsed_hours=4.0,
            ),
        ),
        note="验证反复过载/恢复时的滞回与数值稳定性。",
    ),
)

STEP_COUNT = sum(len(scenario.steps) for scenario in SCENARIOS)
