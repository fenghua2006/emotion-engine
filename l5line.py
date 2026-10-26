"""L5 耐受窗场景 runner。

对 pre-L5 代码运行 legacy 模式时只传旧 kwargs；对 L5 代码运行
current 模式时才传 scar_pressure_by_tag / meta_load_bias。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping

from l5_events import BASE, L5Scenario, L5Step, SCENARIOS
from tolerance import ToleranceConfig, ToleranceState


def _make_config(config: Mapping[str, object], *,
                 legacy: bool) -> ToleranceConfig:
    kwargs = dict(config)
    if legacy:
        # pre-L5 的 ToleranceConfig 没有 capacity_model 字段；有字段时强制 scalar。
        fields = getattr(ToleranceConfig, "__dataclass_fields__", {})
        if "capacity_model" in fields:
            kwargs["capacity_model"] = "scalar"
    return ToleranceConfig(**kwargs)


def snapshot_tolerance(tolerance: ToleranceState, *,
                       legacy: bool = False) -> Dict[str, object]:
    """返回未取整的 tolerance 快照；legacy 只保留旧六个键。"""
    data: Dict[str, object] = {
        "load": float(tolerance.load),
        "capacity": float(tolerance.capacity),
        "zone": tolerance.zone,
        "overload_ratio": float(tolerance.overload_ratio),
        "pending_zone": tolerance.pending_zone,
        "pending_ticks": int(tolerance.pending_ticks),
    }
    if not legacy:
        data["capacity_model"] = tolerance.capacity_model
        data["capacity_by_domain"] = {
            domain: float(value)
            for domain, value in tolerance.capacity_by_domain.items()
        }
        data["meta_load_bias"] = float(tolerance.meta_load_bias)
    return data


def _update_once(tolerance: ToleranceState, step: L5Step,
                 *, legacy: bool) -> None:
    kwargs = {
        "state": dict(step.state),
        "baseline": BASE,
        "trust": float(step.state["trust"]),
        "love": float(step.state["love"]),
        "trust_target_value": step.trust_target_value,
        "neuroticism": step.neuroticism,
        "atmosphere": step.atmosphere,
        "shock_channels": step.shock_channels,
        "boundary_violation": step.boundary_violation,
        "scar_pressure": step.scar_pressure,
        "elapsed_hours": step.elapsed_hours,
    }
    if not legacy:
        kwargs["scar_pressure_by_tag"] = step.scar_pressure_by_tag
        kwargs["meta_load_bias"] = step.meta_load_bias
    tolerance.update(**kwargs)


def run_sequence(steps: Iterable[L5Step], *,
                 legacy: bool = False) -> List[Dict[str, object]]:
    """按顺序运行任意步骤序列，返回每一步的 tolerance 快照。

    用于临时 A/B 序列；冻结 golden 仍走 ``run_scenario``，以保留
    场景级 config 与 label/note 元数据。
    """
    tolerance = ToleranceState(config=_make_config({}, legacy=legacy))
    results: List[Dict[str, object]] = []
    for step in steps:
        _update_once(tolerance, step, legacy=legacy)
        results.append({
            "key": step.key,
            "tolerance": snapshot_tolerance(tolerance, legacy=legacy),
        })
    return results


def run_scenario(scenario: L5Scenario, *,
                 legacy: bool = False) -> List[Dict[str, object]]:
    """按顺序运行一个场景，返回每一步的 tolerance 快照。"""
    tolerance = ToleranceState(
        config=_make_config(scenario.config, legacy=legacy),
    )
    results: List[Dict[str, object]] = []
    for step in scenario.steps:
        _update_once(tolerance, step, legacy=legacy)
        results.append({
            "key": step.key,
            "tolerance": snapshot_tolerance(tolerance, legacy=legacy),
        })
    return results


def run_all(*, legacy: bool = False) -> Dict[str, Dict[str, object]]:
    """运行全部冻结场景，保持 SCENARIOS 的定义顺序。"""
    return {
        scenario.key: {
            "label": scenario.label,
            "note": scenario.note,
            "steps": run_scenario(scenario, legacy=legacy),
        }
        for scenario in SCENARIOS
    }
