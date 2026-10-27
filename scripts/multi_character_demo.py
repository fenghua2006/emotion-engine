#!/usr/bin/env python3
"""L7 v0 demo：A 的 appraisal 如何有界地影响 B 的情绪。

运行：
    python scripts/multi_character_demo.py
    python scripts/multi_character_demo.py --gain 0.3 --cap 0.1
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contagion import (  # noqa: E402
    DEFAULT_CONTAGION_CAP,
    DEFAULT_CONTAGION_GAIN,
    ContagionPair,
)
from core import Appraisal, EmotionalState, Personality  # noqa: E402
from engine import (  # noqa: E402
    EmotionEngine,
    MemoryStore,
    SensitizationStore,
)


NOW = 1_800_000_000.0
CHANNELS = ("joy", "sadness", "fear", "trust")


def make_engine(personality: Personality) -> EmotionEngine:
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    state._last_update = NOW
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


def show_state(label: str, result: dict) -> None:
    state = result["state"]
    values = "  ".join(
        f"{channel}={state[channel]:.3f}" for channel in CHANNELS
    )
    print(f"{label:<26}{values}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="L7 multi-character emotional contagion demo",
    )
    parser.add_argument(
        "--gain", type=float, default=DEFAULT_CONTAGION_GAIN,
        help=f"transmission gain (default {DEFAULT_CONTAGION_GAIN})",
    )
    parser.add_argument(
        "--cap", type=float, default=DEFAULT_CONTAGION_CAP,
        help=f"per-dimension cap (default {DEFAULT_CONTAGION_CAP})",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    # A 是高外向 / 高神经质的角色；B 更内向、宜人。
    source_personality = Personality(
        openness=0.8, extraversion=0.7,
        agreeableness=0.7, neuroticism=0.6,
    )
    target_personality = Personality(
        openness=0.6, extraversion=0.25,
        agreeableness=0.8, neuroticism=0.45,
    )

    source = make_engine(source_personality)
    target = make_engine(target_personality)
    control = make_engine(target_personality)
    source_control = make_engine(source_personality)
    pair = ContagionPair(
        source, target, gain=args.gain, cap=args.cap,
    )

    # A 被当众否定；B 只是旁观者，自己的评估没有 A 那么负面。
    source_event = Appraisal(
        goal_relevance=0.9,
        goal_conduciveness=-0.8,
        expectedness=0.2,
        coping_potential=0.15,
        other_agency=0.7,
        social_evaluation=-0.6,
    )
    target_event = Appraisal(
        goal_relevance=0.7,
        goal_conduciveness=0.2,
        expectedness=0.6,
        coping_potential=0.6,
        other_agency=0.4,
        social_evaluation=0.1,
    )

    source_result, target_result = pair.tick_pair(
        source_event, target_event, now=NOW,
    )
    control_result = control.tick(target_event, now=NOW)
    source_plain = source_control.tick(source_event, now=NOW)

    print("=== L7 multi-character contagion ===")
    print("A: 高外向 / 高神经质；B: 内向 / 高宜人")
    print(f"gain={pair.gain:.3f}  cap={pair.cap:.3f}")
    print()
    show_state("A after event", source_result)
    show_state("B alone (control)", control_result)
    show_state("B after contagion", target_result)
    print()
    print("B contagion bias:", target_result.get("contagion_bias", {}))
    print("A unaffected by B:", source_result == source_plain)
    print()
    print(
        "B gated joy/sadness:",
        f"{target_result['gated_appraisal']['joy']:.3f} / "
        f"{target_result['gated_appraisal']['sadness']:.3f}",
    )
    print(
        "control gated joy/sadness:",
        f"{control_result['gated_appraisal']['joy']:.3f} / "
        f"{control_result['gated_appraisal']['sadness']:.3f}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
