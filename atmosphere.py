"""L3 控制层纯函数：氛围漂移 / 调制，以及有界伤疤放大。

这一层不持有状态，也不导入 engine / tolerance / memory / SQLite。
`EmotionEngine` 负责决定调用顺序；这里只提供可单独测试的数值变换。

修正后的顺序：
  - 上一 tick 结束时的 atmosphere 调制本 tick 的 gated appraisal；
  - 本 tick 检测到 shock 后，更新 atmosphere，供下一 tick 使用。

legacy 路径仍可通过 `clamp=False` 与旧调用顺序复现 pre-L3 行为。
"""

from __future__ import annotations

import math
from typing import Dict, List, Mapping, Tuple

from core import Channel

ATMOSPHERE_MIN = -1.0
ATMOSPHERE_MAX = 1.0
ATMOSPHERE_DRIFT_LIMIT = 0.1
ATMOSPHERE_BASE_TARGET = -0.6
ATMOSPHERE_SHOCK_GAIN = 1.2
ATMOSPHERE_AROUSAL_GAIN = 0.4
ATMOSPHERE_BOUNDARY_BONUS = 0.3

TENSE_NEGATIVE_GAIN = 0.4
TENSE_JOY_DAMP = 0.3
RELAXED_NEGATIVE_BUFFER = 0.3
RELAXED_JOY_BOOST = 0.2

SCAR_CHANNELS = (Channel.SADNESS, Channel.FEAR, Channel.ANGER)
SCAR_CHANNEL_NAMES = {ch.value for ch in SCAR_CHANNELS}
MAX_SCAR_SHIFT = 0.6
SCAR_SHIFT_EPS = 0.01
SCAR_VALUE_EPS = 0.001


def _channel_name(channel) -> str:
    return channel.value if isinstance(channel, Channel) else str(channel)


def _finite_float(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) else number


def clamp_atmosphere(value: float) -> float:
    """把氛围限制在 [-1, 1]。"""
    return max(ATMOSPHERE_MIN, min(ATMOSPHERE_MAX, _finite_float(value)))


def atmosphere_target(shock_density: float, arousal: float,
                      boundary_violation: bool = False) -> float:
    """氛围目标：shock 密度 + 当前唤醒度 + 越界惩罚。"""
    target = (
        ATMOSPHERE_BASE_TARGET
        + _finite_float(shock_density) * ATMOSPHERE_SHOCK_GAIN
        + _finite_float(arousal) * ATMOSPHERE_AROUSAL_GAIN
    )
    if boundary_violation:
        target += ATMOSPHERE_BOUNDARY_BONUS
    return target


def drift_atmosphere(current: float, *,
                     shock_density: float,
                     arousal: float,
                     boundary_violation: bool = False,
                     clamp: bool = True) -> float:
    """单步漂移，最多移动 ±0.1。

    `clamp=False` 只用于 legacy 兼容路径；normalized 路径默认把结果
    限制在 [-1, 1]。
    """
    current = _finite_float(current)
    target = atmosphere_target(shock_density, arousal, boundary_violation)
    step = max(-ATMOSPHERE_DRIFT_LIMIT,
               min(ATMOSPHERE_DRIFT_LIMIT, target - current))
    value = current + step
    return clamp_atmosphere(value) if clamp else value


def apply_atmosphere_modulation(
    gated: Mapping,
    atmosphere: float,
) -> Tuple[Dict, Dict[str, float]]:
    """返回 (新 gated, 实际乘数表)，不修改输入。

    只影响 joy / sadness / fear / anger。紧绷时负面放大、喜悦打折；
    轻松时负面缓冲、喜悦放大。
    """
    result = dict(gated)
    multipliers: Dict[str, float] = {}
    atm = _finite_float(atmosphere)
    if abs(atm) < 1e-12:
        return result, multipliers

    for channel, value in gated.items():
        name = _channel_name(channel)
        if value <= SCAR_VALUE_EPS:
            continue
        factor = 1.0
        if name == "joy":
            damp = TENSE_JOY_DAMP if atm > 0 else RELAXED_JOY_BOOST
            factor = 1.0 - atm * damp
        elif name in SCAR_CHANNEL_NAMES:
            gain = TENSE_NEGATIVE_GAIN if atm > 0 else RELAXED_NEGATIVE_BUFFER
            factor = 1.0 + atm * gain
        if factor != 1.0:
            result[channel] = value * factor
            multipliers[name] = factor
    return result, multipliers


def clamp_scar_shift(shift: float) -> float:
    """伤疤有效放大上限 60%，且不允许负值。"""
    return max(0.0, min(MAX_SCAR_SHIFT, _finite_float(shift)))


def apply_scar_amplification(
    gated: Mapping,
    shift: float,
    goal_conducibility: float,
) -> Tuple[Dict, float, List[str]]:
    """返回 (新 gated, effective_shift, 被放大的通道名)。

    只有负面事件、且只放大 sadness / fear / anger；上限 60%。
    """
    result = dict(gated)
    effective = clamp_scar_shift(shift)
    if goal_conducibility >= 0 or effective <= SCAR_SHIFT_EPS:
        return result, effective, []

    factor = 1.0 + effective
    amplified: List[str] = []
    for channel, value in gated.items():
        name = _channel_name(channel)
        if name in SCAR_CHANNEL_NAMES and value > SCAR_VALUE_EPS:
            result[channel] = value * factor
            amplified.append(name)
    return result, effective, amplified
