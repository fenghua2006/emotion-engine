"""生成 / 校验 L1 六通道 golden set。

用法：
  python scripts/generate_l1_golden.py                   # 写入相对激活 golden
  python scripts/generate_l1_golden.py --check           # 校验相对激活 golden
  python scripts/generate_l1_golden.py --legacy          # 写入 v0.7 旧行为 golden
  python scripts/generate_l1_golden.py --legacy --check  # 校验旧行为 golden

golden 是行为回归基线，不是“正确答案”。只有**有意**改变 L0 公式或
L1 参考线时才能重新生成，并在 diff 中逐条检查变化。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import Personality  # noqa: E402
from fastline import (  # noqa: E402
    DEFAULT_LOVE,
    DEFAULT_TRUST,
    DOMINANT_THRESHOLD,
    FastLine,
    FastLineStep,
    SIX_CHANNEL_NAMES,
)
from l1_events import EVENTS  # noqa: E402

GOLDEN_PATH = ROOT / "fixtures" / "l1_golden.json"
LEGACY_GOLDEN_PATH = ROOT / "fixtures" / "l1_golden_legacy.json"
ELAPSED_MINUTES = 10.0


def _rounded(value: float) -> float:
    return round(value, 12)


def _step_payload(step: FastLineStep, *, include_appraisal: bool) -> dict:
    payload = {
        "values": {k: _rounded(v) for k, v in step.values.items()},
        "dominant": step.dominant,
        "shock_channels": list(step.shock_channels),
        "blends": list(step.blends),
    }
    if include_appraisal:
        payload["raw_appraisal"] = {
            k: _rounded(v) for k, v in step.raw_appraisal.items()
        }
        payload["gated_appraisal"] = {
            k: _rounded(v) for k, v in step.gated_appraisal.items()
        }
    return payload


def _make_line(personality: Personality, *, legacy: bool) -> FastLine:
    if legacy:
        # v0.7 对照：绝对阈值交互，且空闲 tick 不跑交互。
        return FastLine(
            personality=personality,
            legacy_interactions=True,
            idle_interactions=False,
        )
    return FastLine(personality=personality)


def build_golden(*, legacy: bool = False) -> dict:
    personality = Personality()
    isolated = {}
    for event in EVENTS:
        line = _make_line(personality, legacy=legacy)
        step = line.step(
            event.appraisal,
            elapsed_minutes=ELAPSED_MINUTES,
            key=event.key,
        )
        isolated[event.key] = _step_payload(step, include_appraisal=True)

    line = _make_line(personality, legacy=legacy)
    sequence = [
        {"key": event.key, **_step_payload(step, include_appraisal=False)}
        for event, step in zip(
            EVENTS,
            line.run(EVENTS, elapsed_minutes=ELAPSED_MINUTES),
        )
    ]

    return {
        "meta": {
            "layer": "L1",
            "description": "六条快通道参考线；固定人格、固定 trust/love、固定 10 分钟步长。",
            "personality": {
                "openness": personality.openness,
                "conscientiousness": personality.conscientiousness,
                "extraversion": personality.extraversion,
                "agreeableness": personality.agreeableness,
                "neuroticism": personality.neuroticism,
            },
            "trust": DEFAULT_TRUST,
            "love": DEFAULT_LOVE,
            "elapsed_minutes": ELAPSED_MINUTES,
            "channels": list(SIX_CHANNEL_NAMES),
            "dominant_threshold": DOMINANT_THRESHOLD,
            "event_count": len(EVENTS),
            "interaction_mode": "legacy_absolute" if legacy else "relative_to_baseline",
            "generated_by": "scripts/generate_l1_golden.py",
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
                        help="使用 v0.7 绝对阈值交互，并读写 legacy golden")
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
