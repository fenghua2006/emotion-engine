"""生成 / 校验 L2 慢通道 golden set。

用法：
  python scripts/generate_l2_golden.py                   # 写入 L2 golden
  python scripts/generate_l2_golden.py --check           # 校验 L2 golden
  python scripts/generate_l2_golden.py --legacy          # 写入旧慢通道行为 golden
  python scripts/generate_l2_golden.py --legacy --check  # 校验旧行为 golden

golden 是行为回归基线，不是“正确答案”。只有**有意**改变慢通道公式或
slow → fast 语义时才能重新生成，并在 diff 中逐条检查变化。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import GUILT_SELF_AGENCY_THRESHOLD, Personality  # noqa: E402
from l2_events import EVENTS  # noqa: E402
from slowline import L2Step, SlowLine  # noqa: E402

GOLDEN_PATH = ROOT / "fixtures" / "l2_golden.json"
LEGACY_GOLDEN_PATH = ROOT / "fixtures" / "l2_golden_legacy.json"


def _rounded(value: float) -> float:
    return round(value, 12)


def _step_payload(step: L2Step) -> dict:
    return {
        "mode": step.mode,
        "values": {k: _rounded(v) for k, v in step.values.items()},
        "dominant": step.dominant,
        "shock_channels": list(step.shock_channels),
        "blends": list(step.blends),
        "raw_appraisal": {
            k: _rounded(v) for k, v in step.raw_appraisal.items()
        },
        "gated_appraisal": {
            k: _rounded(v) for k, v in step.gated_appraisal.items()
        },
        "trust_target": _rounded(step.trust_target),
        "slow_positive_input": step.slow_positive_input,
    }


def _make_line(personality: Personality, *, legacy: bool) -> SlowLine:
    if legacy:
        return SlowLine(
            personality=personality,
            legacy_slow=True,
            legacy_interactions=True,
        )
    return SlowLine(personality=personality)


def build_golden(*, legacy: bool = False) -> dict:
    personality = Personality()

    isolated = {}
    for event in EVENTS:
        line = _make_line(personality, legacy=legacy)
        if event.offline_hours > 0:
            step = line.offline(event.offline_hours, key=event.key)
        else:
            step = line.step(
                event.appraisal,
                elapsed_minutes=event.elapsed_minutes,
                boundary_violation=event.boundary_violation,
                key=event.key,
            )
        isolated[event.key] = _step_payload(step)

    line = _make_line(personality, legacy=legacy)
    sequence = [
        {"key": event.key, **_step_payload(step)}
        for event, step in zip(EVENTS, line.run(EVENTS))
    ]

    return {
        "meta": {
            "layer": "L2",
            "description": "trust / love / longing / guilt 参考线；固定人格与时钟。",
            "personality": {
                "openness": personality.openness,
                "conscientiousness": personality.conscientiousness,
                "extraversion": personality.extraversion,
                "agreeableness": personality.agreeableness,
                "neuroticism": personality.neuroticism,
            },
            "channels": [
                "joy", "sadness", "anger", "fear", "love",
                "disgust", "surprise", "trust", "longing", "guilt",
            ],
            "event_count": len(EVENTS),
            "guilt_self_agency_threshold": (
                0.0 if legacy else GUILT_SELF_AGENCY_THRESHOLD
            ),
            "positive_event_input": (
                "cumulative_legacy" if legacy else "per_tick"
            ),
            "trust_interaction_baseline": (
                "personality" if legacy else "trust_target(love)"
            ),
            "love_interaction_baseline": "personality",
            "interaction_mode": (
                "legacy_absolute" if legacy else "relative_to_baseline"
            ),
            "generated_by": "scripts/generate_l2_golden.py",
            "note": "golden 是当前行为基线，不是理论正确值；变更必须人工审阅。",
        },
        "isolated": isolated,
        "sequence": sequence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="只比较现有 golden，不写文件")
    parser.add_argument("--legacy", action="store_true",
                        help="使用旧慢通道 + 绝对阈值交互，读写 legacy golden")
    args = parser.parse_args()

    golden = build_golden(legacy=args.legacy)
    text = json.dumps(golden, ensure_ascii=False, indent=2) + "\n"
    path = LEGACY_GOLDEN_PATH if args.legacy else GOLDEN_PATH

    if args.check:
        if not path.exists():
            print(f"missing golden: {path}")
            return 1
        current = path.read_text(encoding="utf-8")
        if current != text:
            print(f"golden drifted: {path}")
            return 1
        print(f"golden ok: {path}")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path} ({len(EVENTS)} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
