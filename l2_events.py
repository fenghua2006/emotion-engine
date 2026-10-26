"""L2 慢通道 golden set 的冻结事件清单。

覆盖：
  - trust 向 trust_target(love) 收敛；
  - love 在信任健康时生长、信任缺口大时侵蚀；
  - 背叛导致 trust 腰斩、严重时 love 二级受损；
  - longing 离线生长、在线消退；
  - guilt 在 self_agency ≤ 0.3 时为 0，> 0.3 才激活；
  - slow → fast：trust 高于关系均衡线时抑制 fear；
  - 长时间离线：trust 7 天后微降。

这些 Appraisal 是冻结副本，不从角色模块动态导入。角色文件以后调整
触发值时，L2 golden 不会悄悄漂移；要改必须显式改这里并重新生成。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from core import Appraisal


@dataclass(frozen=True)
class L2Event:
    key: str
    source: str
    label: str
    appraisal: Optional[Appraisal] = None
    elapsed_minutes: float = 10.0
    boundary_violation: bool = False
    offline_hours: float = 0.0
    note: str = ""


def _online(key: str, source: str, label: str,
            note: str = "", elapsed_minutes: float = 10.0,
            boundary_violation: bool = False,
            **appraisal) -> L2Event:
    return L2Event(
        key=key,
        source=source,
        label=label,
        appraisal=Appraisal(**appraisal),
        elapsed_minutes=elapsed_minutes,
        boundary_violation=boundary_violation,
        note=note,
    )


def _offline(key: str, source: str, label: str,
             hours: float, note: str = "") -> L2Event:
    return L2Event(
        key=key,
        source=source,
        label=label,
        offline_hours=hours,
        note=note,
    )


EVENTS: Tuple[L2Event, ...] = (
    # ── 在线：基线附近 ──────────────────────────────────────
    _online(
        "l2.routine_notice", "synthetic", "例行通知",
        goal_relevance=0.1, goal_conduciveness=0.0,
        expectedness=1.0, other_agency=0.0,
        coping_potential=0.8, social_evaluation=0.0,
        note="低相关弱事件；slow channels 不应被明显推动。",
    ),

    # ── 在线：正面关系事件 ──────────────────────────────────
    _online(
        "l2.praised_by_trusted", "synthetic", "被信任的人夸奖",
        goal_relevance=0.7, goal_conduciveness=0.7,
        expectedness=0.4, other_agency=0.8,
        coping_potential=0.4, social_evaluation=0.8,
        note="正面外部事件；love 应生长，trust 向目标靠近。",
    ),
    _online(
        "l2.shared_quiet_time", "synthetic", "安静的共处",
        goal_relevance=0.8, goal_conduciveness=0.5,
        expectedness=0.9, other_agency=0.5,
        coping_potential=0.7, social_evaluation=0.5,
        note="低唤醒正面事件；用于观察 love 的缓慢累积。",
    ),

    # ── 在线：负面关系事件 ──────────────────────────────────
    _online(
        "l2.cold_shoulder", "synthetic", "被冷落",
        goal_relevance=0.8, goal_conduciveness=-0.4,
        expectedness=0.7, other_agency=0.9,
        coping_potential=0.4, social_evaluation=-0.3,
        note="轻度负面外部事件；anger 应压低 trust，但不触发背叛。",
    ),
    _online(
        "l2.betrayal", "synthetic", "被背叛",
        goal_relevance=0.9, goal_conduciveness=-0.9,
        expectedness=0.5, other_agency=0.9,
        coping_potential=0.2, social_evaluation=-0.7,
        note="严重背叛；damage_trust 腰斩 trust，严重时二级侵蚀 love。",
    ),

    # ── 在线：guilt 门槛 ────────────────────────────────────
    _online(
        "l2.external_blame", "synthetic", "完全是对方的错",
        goal_relevance=0.9, goal_conduciveness=-0.8,
        expectedness=0.5, other_agency=0.95,
        coping_potential=0.3, social_evaluation=-0.4,
        note="self_agency=0.05；guilt 必须为 0。",
    ),
    _online(
        "l2.low_self_agency", "synthetic", "只有一点点自己的责任",
        goal_relevance=0.9, goal_conduciveness=-0.8,
        expectedness=0.5, other_agency=0.75,
        coping_potential=0.3, social_evaluation=-0.4,
        note="self_agency=0.25 < 0.3；guilt 必须为 0。",
    ),
    _online(
        "l2.self_blame", "synthetic", "是我搞砸的",
        goal_relevance=0.9, goal_conduciveness=-0.8,
        expectedness=0.5, other_agency=0.1,
        coping_potential=0.5, social_evaluation=-0.4,
        note="self_agency=0.9；guilt 应显著激活。",
    ),

    # ── 在线：其他快通道边界 ────────────────────────────────
    _online(
        "l2.boundary_violation", "synthetic", "低信任时被越界称呼",
        goal_relevance=0.7, goal_conduciveness=-0.1,
        expectedness=0.3, other_agency=0.8,
        coping_potential=0.5, social_evaluation=0.4,
        boundary_violation=True,
        note="boundary_violation 应压低 trust / joy，抬高 disgust。",
    ),
    _online(
        "l2.unexpected_threat", "synthetic", "完全意外的威胁",
        goal_relevance=1.0, goal_conduciveness=-0.8,
        expectedness=0.0, other_agency=0.7,
        coping_potential=0.1, social_evaluation=-0.3,
        note="高意外低应对；surprise / fear 上界样本。",
    ),
    _online(
        "l2.social_humiliation", "synthetic", "当众被羞辱",
        goal_relevance=0.8, goal_conduciveness=0.0,
        expectedness=0.5, other_agency=0.7,
        coping_potential=0.5, social_evaluation=-0.9,
        note="结果中性、社交评价极负；disgust 上界样本。",
    ),

    # ── 离线：思念与信任时间尺度 ────────────────────────────
    _offline(
        "l2.offline_2h", "synthetic", "离线 2 小时",
        2.0, note="longing 开始生长；love 几乎不变。",
    ),
    _online(
        "l2.reunion", "synthetic", "重逢",
        goal_relevance=0.8, goal_conduciveness=0.6,
        expectedness=0.6, other_agency=0.6,
        coping_potential=0.6, social_evaluation=0.7,
        elapsed_minutes=10.0,
        note="在线消退 longing；正面事件让 love 继续生长。",
    ),
    _offline(
        "l2.offline_24h", "synthetic", "离线 24 小时",
        24.0, note="longing 显著生长；trust 尚未进入 7 天衰减。",
    ),
    _offline(
        "l2.offline_8d", "synthetic", "离线 8 天",
        8 * 24.0, note="刚越过 7 天；trust 开始微降。",
    ),
    _offline(
        "l2.offline_30d", "synthetic", "离线 30 天",
        30 * 24.0, note="longing 受 love × 2 上限约束；trust 继续微降。",
    ),

    # ── 在线：回归后的修复与再次自责 ────────────────────────
    _online(
        "l2.after_long_absence", "synthetic", "长期缺席后重新出现",
        goal_relevance=0.9, goal_conduciveness=0.5,
        expectedness=0.5, other_agency=0.7,
        coping_potential=0.5, social_evaluation=0.4,
        note="正面但带不确定性；观察 love / trust 修复速度。",
    ),
    _online(
        "l2.repair_self_blame", "synthetic", "为离开道歉",
        goal_relevance=0.9, goal_conduciveness=-0.5,
        expectedness=0.2, other_agency=0.1,
        coping_potential=0.7, social_evaluation=-0.3,
        note="guilt 再次激活；guilt→trust / sadness / love 规则生效。",
    ),
    _online(
        "l2.final_neutral", "synthetic", "平静收尾",
        goal_relevance=0.2, goal_conduciveness=0.0,
        expectedness=1.0, other_agency=0.1,
        coping_potential=0.8, social_evaluation=0.0,
        note="低相关收尾，检查慢通道不会因空闲而漂移。",
    ),
)


EVENT_BY_KEY = {event.key: event for event in EVENTS}


def event_by_key(key: str) -> L2Event:
    return EVENT_BY_KEY[key]
