"""生成 / 校验 L5 耐受窗 golden set。

用法：
  python scripts/generate_l5_tolerance_golden.py                   # 写入 current golden
  python scripts/generate_l5_tolerance_golden.py --check           # 校验 current golden
  python scripts/generate_l5_tolerance_golden.py --legacy          # 写入 pre-L5 golden
  python scripts/generate_l5_tolerance_golden.py --legacy --check  # 校验 pre-L5 golden

legacy golden 必须在修改 tolerance.py 之前从当前代码冻结；它代表
pre-L5 的标量容量 / global-max 伤疤 / 无 meta bias 行为。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import fields
from typing import Dict, List

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tolerance  # noqa: E402
from l5_events import SCENARIOS, STEP_COUNT  # noqa: E402
from l5line import run_all  # noqa: E402

CURRENT_PATH = ROOT / "fixtures" / "l5_tolerance_golden.json"
LEGACY_PATH = ROOT / "fixtures" / "l5_tolerance_golden_legacy.json"

OLD_CONFIG_FIELDS = (
    "base_capacity",
    "trust_capacity",
    "love_capacity",
    "neuroticism_narrowing",
    "trust_deficit_load",
    "trust_deficit_deadband",
    "shock_load",
    "boundary_load",
    "scar_load",
    "recovery_hours",
    "hysteresis_ticks",
    "recovery_ratio",
    "min_capacity",
    "max_capacity",
    "max_load",
)


def _rounded(value):
    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    return value


def _config_payload(*, legacy: bool) -> Dict[str, object]:
    config = tolerance.ToleranceConfig()
    if legacy:
        names = OLD_CONFIG_FIELDS
    else:
        names = tuple(field.name for field in fields(tolerance.ToleranceConfig))
    return {name: getattr(config, name) for name in names}


def _meta_payload(*, legacy: bool) -> Dict[str, object]:
    common = {
        "layer": "L5",
        "scenario_count": len(SCENARIOS),
        "step_count": STEP_COUNT,
        "generated_by": "scripts/generate_l5_tolerance_golden.py",
        "note": "golden 是当前行为基线，不是理论正确值；变更必须人工审阅。",
    }
    if legacy:
        common.update({
            "mode": "legacy",
            "capacity_model": "scalar",
            "scar_mode": "global_max",
            "meta_load_consumed": False,
            "config": _config_payload(legacy=True),
        })
        return common

    config = tolerance.ToleranceConfig()
    common.update({
        "mode": "current",
        "capacity_model": config.capacity_model,
        "domain_names": list(tolerance.DOMAIN_NAMES),
        "domain_weights": dict(tolerance.DOMAIN_WEIGHTS),
        "scar_domain_map": {
            domain: list(tags)
            for domain, tags in tolerance.SCAR_DOMAIN_MAP.items()
        },
        "domain_scar_load": config.domain_scar_load,
        "meta_load_cap": config.meta_load_cap,
        "config": _config_payload(legacy=False),
    })
    return common


def build_golden(*, legacy: bool = False) -> Dict[str, object]:
    scenarios = run_all(legacy=legacy)
    payload: Dict[str, object] = {}
    for key, scenario in scenarios.items():
        steps: List[Dict[str, object]] = []
        for step in scenario["steps"]:
            steps.append({
                "key": step["key"],
                "tolerance": _rounded(step["tolerance"]),
            })
        payload[key] = {
            "label": scenario["label"],
            "note": scenario["note"],
            "steps": steps,
        }
    return {
        "meta": _meta_payload(legacy=legacy),
        "scenarios": payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="只比较现有 golden，不写文件")
    parser.add_argument("--legacy", action="store_true",
                        help="读写 pre-L5 标量 golden")
    args = parser.parse_args()

    golden = build_golden(legacy=args.legacy)
    text = json.dumps(golden, ensure_ascii=False, indent=2) + "\n"
    path = LEGACY_PATH if args.legacy else CURRENT_PATH

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
    print(f"wrote {path} ({len(SCENARIOS)} scenarios, {STEP_COUNT} steps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
