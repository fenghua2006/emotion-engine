"""回放一条事件并打印 StepTrace。

示例：
  python scripts/replay_trace.py --text "没想到你居然离开了" --character furina
  python scripts/replay_trace.py --appraisal '{"goal_relevance":0.8,"goal_conduciveness":-0.7}'

只读回放：每次命令都新建引擎，不写快照、不写记忆。输出 JSON，退出码 0。
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys
from typing import Any, Dict, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import Appraisal  # noqa: E402
from engine import (  # noqa: E402
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SensitizationStore,
)
from jev import make_appraisal  # noqa: E402
from noul import detect as detect_boundary  # noqa: E402


def _default_engine() -> EmotionEngine:
    return EmotionEngine(
        state=EmotionalState(),
        personality=Personality(),
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


def build_engine(character_id: str) -> EmotionEngine:
    if not character_id or character_id == "default":
        return _default_engine()
    try:
        module = importlib.import_module(f"characters.{character_id}")
        factory = getattr(module, f"create_{character_id}")
        engine = factory()
        if isinstance(engine, EmotionEngine):
            return engine
    except Exception as exc:
        print(f"[replay] character fallback: {exc}", file=sys.stderr)
    return _default_engine()


def _appraisal_from_args(args: argparse.Namespace) -> Optional[Appraisal]:
    if args.appraisal:
        payload = json.loads(args.appraisal)
        if not isinstance(payload, dict):
            raise ValueError("--appraisal must be a JSON object")
        return make_appraisal(payload)
    if args.text:
        from jev import analyze
        return analyze(args.text).appraisal
    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="回放一条事件并打印 StepTrace")
    parser.add_argument("--text", default="", help="用 JEV 评估的自然语言事件")
    parser.add_argument("--appraisal", default="", help="直接传入六维 JSON")
    parser.add_argument("--character", default="default")
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--boundary", action="store_true", help="强制边界侵犯")
    parser.add_argument("--now", type=float, default=None, help="注入时间戳，保证确定性")
    args = parser.parse_args(argv)

    if not args.text and not args.appraisal:
        parser.error("--text or --appraisal is required")

    appraisal = _appraisal_from_args(args)
    boundary = bool(args.boundary)
    if not boundary and args.text:
        boundary = detect_boundary(args.text).violated

    engine = build_engine(args.character)
    steps: list[Dict[str, Any]] = []
    for _ in range(max(1, args.steps)):
        result = engine.tick(
            appraisal,
            boundary_violation=boundary,
            now=args.now,
            trace=True,
        )
        steps.append(result)

    output = {
        "character": args.character,
        "text": args.text,
        "boundary_violation": boundary,
        "steps": steps,
        "final_state": engine.state.to_dict(),
        "atmosphere": round(engine.atmosphere, 6),
        "tolerance": engine.tolerance.to_dict(),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
