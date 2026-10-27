"""L7.3 / L7.4 演示：群体传播链 + 方向性关系强度。

运行：
    python scripts/contagion_network_demo.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contagion import (  # noqa: E402
    ContagionEdge,
    ContagionNetwork,
    ContagionPair,
    DirectionalRelationship,
)
from core import Appraisal, EmotionalState, Personality  # noqa: E402
from engine import EmotionEngine, MemoryStore, SensitizationStore  # noqa: E402


NOW = 1_800_000_000.0


def make_engine(trust: float = 0.25, love: float = 0.2) -> EmotionEngine:
    personality = Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    state.trust = trust
    state.love = love
    state._last_update = NOW
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


def negative_appraisal() -> Appraisal:
    return Appraisal(
        goal_relevance=0.8,
        goal_conduciveness=-0.6,
        expectedness=0.2,
        coping_potential=0.2,
        other_agency=0.6,
        social_evaluation=-0.4,
    )


def positive_appraisal() -> Appraisal:
    return Appraisal(
        goal_relevance=0.7,
        goal_conduciveness=0.3,
        expectedness=0.6,
        coping_potential=0.6,
        other_agency=0.4,
        social_evaluation=0.2,
    )


def print_biases(title, biases):
    print(title)
    if not biases:
        print("  （无）")
        return
    for name, bias in biases.items():
        print(f"  {name}: {bias}")


def main() -> int:
    print("=" * 64)
    print("L7.3 群体传播：A -> B -> C，hop_decay=0.5")
    print("=" * 64)
    network = ContagionNetwork(
        {
            "A": make_engine(trust=0.8, love=1.2),
            "B": make_engine(trust=0.5, love=0.6),
            "C": make_engine(trust=0.3, love=0.3),
        },
        [
            ContagionEdge("A", "B", 0.8),
            ContagionEdge("B", "C", 0.6),
        ],
        hop_decay=0.5,
        max_hops=3,
    )
    network_bias = network.derive({"A": negative_appraisal()})
    print_biases("每个节点收到的感染偏置：", network_bias)
    print("  说明：B 为第一跳 0.8；C 再乘 0.6 * 0.5，强度继续衰减。")

    print()
    print("=" * 64)
    print("L7.4 方向性关系：A -> B 与 B -> A 各自独立")
    print("=" * 64)
    source_relationship = DirectionalRelationship(
        trust=0.9,
        love=1.4,
        familiarity=0.8,
        boundaries=("不深夜打扰",),
    )
    target_relationship = DirectionalRelationship(
        trust=0.2,
        love=0.3,
        familiarity=0.1,
    )
    pair = ContagionPair.from_directional_relationship(
        make_engine(),
        make_engine(),
        source_relationship=source_relationship,
        target_relationship=target_relationship,
    )
    print(
        f"strength A -> B: {pair.source_to_target_strength:.3f}  "
        f"B -> A: {pair.target_to_source_strength:.3f}  "
        f"average: {pair.strength:.3f}"
    )

    source_result, target_result = pair.tick_bidirectional(
        negative_appraisal(),
        positive_appraisal(),
        now=NOW,
    )
    print("A 收到 B 的偏置：", source_result.get("contagion_bias", {}))
    print("B 收到 A 的偏置：", target_result.get("contagion_bias", {}))
    print(
        "  说明：A -> B 强度高，B 受到更强影响；B -> A 强度低，"
        "A 只受到较弱影响。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
