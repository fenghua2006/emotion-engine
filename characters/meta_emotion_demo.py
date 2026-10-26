"""L3.5 元情绪 demo：同一个 anger，两种二阶态度。

shame profile: 愤怒 → 羞耻 → 压抑
pride profile: 愤怒 → 骄傲 → 表达

两个引擎使用完全相同的初始状态、事件与时钟。基础通道必须逐值一致；
差异只出现在 trace 的 meta_appraisal / meta_emotions / regulation_bias。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Appraisal
from characters.furina import (
    create_furina,
    furina_meta_pride_profile,
    furina_meta_shame_profile,
)


EVENT = Appraisal(
    goal_relevance=0.9,
    goal_conduciveness=-0.75,
    expectedness=0.4,
    other_agency=0.9,
    coping_potential=0.55,
    social_evaluation=-0.35,
)


def run(profile):
    engine = create_furina(
        meta_emotion_enabled=True,
        meta_emotion_profile=profile,
    )
    engine.state._last_update = 1000.0
    result = engine.tick(EVENT, now=1060.0, trace=True)
    anger = [
        emotion for emotion in result["trace"]["meta_emotions"]
        if emotion["target"] == "anger"
    ]
    return engine, result, anger


def show(label, result, anger):
    state = result["state"]
    print(f"\n-- {label} --")
    print(
        f"  joy={state['joy']:.3f} sad={state['sadness']:.3f} "
        f"anger={state['anger']:.3f} fear={state['fear']:.3f}"
    )
    print(
        f"  trust={state['trust']:.3f} love={state['love']:.3f} "
        f"atmosphere={result['atmosphere']:.3f}"
    )
    if not anger:
        print("  anger meta: (none)")
        return
    for emotion in anger:
        print(
            f"  anger meta: {emotion['kind']} "
            f"intensity={emotion['intensity']:.3f} "
            f"action={emotion['action']}"
        )


def main():
    shame_engine, shame_result, shame_anger = run(
        furina_meta_shame_profile
    )
    pride_engine, pride_result, pride_anger = run(
        furina_meta_pride_profile
    )

    show("Anger -> Shame -> Suppress", shame_result, shame_anger)
    show("Anger -> Pride -> Express", pride_result, pride_anger)

    # 基础通道必须完全一致：元情绪层只增加解释，不改写 primary emotion。
    assert shame_result["state"] == pride_result["state"], (
        "meta profiles must not change primary channels"
    )

    assert shame_anger, "shame profile should activate an anger meta-emotion"
    assert pride_anger, "pride profile should activate an anger meta-emotion"
    assert shame_anger[0]["kind"] == "shame"
    assert shame_anger[0]["action"] == "suppress"
    assert pride_anger[0]["kind"] == "pride"
    assert pride_anger[0]["action"] == "express"

    print("\nOK: same anger, different meta-emotion and regulation.")


if __name__ == "__main__":
    main()
