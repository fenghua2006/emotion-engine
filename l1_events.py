"""L1 golden set 的冻结事件清单。

事件来源：
  - characters/furina.py      11 条
  - characters/kokomi.py       9 条
  - characters/columbina.py   11 条
  - engine.py demo             3 条
  - L1 synthetic edge cases   12 条
合计 46 条，满足计划中的 40–60 条。

这些 Appraisal 是**冻结副本**，不是从角色模块动态导入的。角色文件以后
调整触发值时，L1 golden 不会悄悄跟着变；要改，必须显式改这里并重新生成
golden，diff 里能看见。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from core import Appraisal


@dataclass(frozen=True)
class L1Event:
    key: str
    source: str
    label: str
    appraisal: Appraisal
    note: str = ""


def _event(key: str, source: str, label: str,
           note: str = "", **kwargs) -> L1Event:
    return L1Event(
        key=key,
        source=source,
        label=label,
        appraisal=Appraisal(**kwargs),
        note=note,
    )


EVENTS: Tuple[L1Event, ...] = (
    # ── 芙宁娜：表演 / 独处 / 被看见 ──────────────────────────
    _event(
        "furina.alone", "furina", "独处——不用演了",
        goal_relevance=0.9, goal_conduciveness=0.7,
        expectedness=1.0, other_agency=0.0,
        coping_potential=0.9, social_evaluation=0.0,
    ),
    _event(
        "furina.understood", "furina", "被真正理解",
        goal_relevance=1.0, goal_conduciveness=0.8,
        expectedness=0.1, other_agency=0.9,
        coping_potential=0.2, social_evaluation=0.9,
    ),
    _event(
        "furina.no_more_performance", "furina", "不再需要表演",
        goal_relevance=1.0, goal_conduciveness=1.0,
        expectedness=0.05, other_agency=0.6,
        coping_potential=0.3, social_evaluation=0.5,
    ),
    _event(
        "furina.helped_troupe", "furina", "帮助了剧团（真心投入）",
        goal_relevance=0.7, goal_conduciveness=0.6,
        expectedness=0.6, other_agency=0.3,
        coping_potential=0.7, social_evaluation=0.6,
    ),
    _event(
        "furina.forced_performance", "furina", "被要求公开表演（水神角色）",
        goal_relevance=0.9, goal_conduciveness=-0.3,
        expectedness=1.0, other_agency=0.7,
        coping_potential=0.4, social_evaluation=0.9,
    ),
    _event(
        "furina.trial", "furina", "被质疑/审判",
        goal_relevance=1.0, goal_conduciveness=-0.7,
        expectedness=0.3, other_agency=1.0,
        coping_potential=0.1, social_evaluation=-0.8,
    ),
    _event(
        "furina.secret_risk", "furina", "秘密可能被揭穿",
        goal_relevance=1.0, goal_conduciveness=-0.6,
        expectedness=0.6, other_agency=0.5,
        coping_potential=0.2, social_evaluation=-0.5,
    ),
    _event(
        "furina.stared_at", "furina", "被盯着看",
        goal_relevance=0.5, goal_conduciveness=-0.2,
        expectedness=1.0, other_agency=1.0,
        coping_potential=0.4, social_evaluation=0.3,
    ),
    _event(
        "furina.lonely_memory", "furina", "500年的孤独回忆涌上来",
        goal_relevance=1.0, goal_conduciveness=-0.4,
        expectedness=0.5, other_agency=0.0,
        coping_potential=0.3, social_evaluation=0.0,
    ),
    _event(
        "furina.performance_mode", "furina", "切换为表演模式",
        goal_relevance=0.8, goal_conduciveness=0.3,
        expectedness=1.0, other_agency=0.1,
        coping_potential=0.8, social_evaluation=0.8,
    ),
    _event(
        "furina.alone_after_show", "furina", "表演后独处（面具摘下的瞬间）",
        goal_relevance=0.9, goal_conduciveness=0.5,
        expectedness=1.0, other_agency=0.0,
        coping_potential=0.7, social_evaluation=0.0,
    ),

    # ── 珊瑚宫心海：军师 / 社交焦虑 / 责任 ────────────────────
    _event(
        "kokomi.alone_reading", "kokomi", "独处读书",
        goal_relevance=0.9, goal_conduciveness=0.8,
        expectedness=1.0, other_agency=0.0,
        coping_potential=0.95, social_evaluation=0.0,
    ),
    _event(
        "kokomi.praised_by_trusted", "kokomi", "被信任的人夸奖",
        goal_relevance=0.7, goal_conduciveness=0.6,
        expectedness=0.3, other_agency=0.8,
        coping_potential=0.3, social_evaluation=0.8,
    ),
    _event(
        "kokomi.helped_people", "kokomi", "帮助了民众",
        goal_relevance=0.9, goal_conduciveness=0.9,
        expectedness=0.7, other_agency=0.3,
        coping_potential=0.8, social_evaluation=0.7,
    ),
    _event(
        "kokomi.public_speech", "kokomi", "公开演讲",
        goal_relevance=0.9, goal_conduciveness=-0.3,
        expectedness=1.0, other_agency=0.1,
        coping_potential=0.2, social_evaluation=0.8,
    ),
    _event(
        "kokomi.soldier_lost", "kokomi", "部下牺牲",
        goal_relevance=1.0, goal_conduciveness=-0.9,
        expectedness=0.5, other_agency=0.6,
        coping_potential=0.1, social_evaluation=-0.5,
    ),
    _event(
        "kokomi.leadership_criticized", "kokomi", "被批评领导能力",
        goal_relevance=0.9, goal_conduciveness=-0.7,
        expectedness=0.6, other_agency=1.0,
        coping_potential=0.2, social_evaluation=-0.7,
    ),
    _event(
        "kokomi.unfamiliar_social", "kokomi", "不熟悉的社交场合",
        goal_relevance=0.6, goal_conduciveness=-0.2,
        expectedness=0.4, other_agency=0.5,
        coping_potential=0.1, social_evaluation=0.3,
    ),
    _event(
        "kokomi.battle_plan", "kokomi", "制定作战计划",
        goal_relevance=1.0, goal_conduciveness=0.5,
        expectedness=0.9, other_agency=0.0,
        coping_potential=0.8, social_evaluation=0.5,
    ),
    _event(
        "kokomi.alone_with_traveler", "kokomi", "和旅行者独处",
        goal_relevance=0.6, goal_conduciveness=0.4,
        expectedness=0.5, other_agency=0.4,
        coping_potential=0.5, social_evaluation=0.4,
    ),

    # ── 哥伦比娅：月神 / 疏离 / 被需要 ────────────────────────
    _event(
        "columbina.alone", "columbina", "独处",
        goal_relevance=0.6, goal_conduciveness=0.7,
        expectedness=1.0, other_agency=0.0,
        coping_potential=0.9, social_evaluation=0.0,
    ),
    _event(
        "columbina.with_traveler", "columbina", "和主角在一起",
        goal_relevance=0.8, goal_conduciveness=0.5,
        expectedness=0.3, other_agency=0.5,
        coping_potential=0.4, social_evaluation=0.5,
    ),
    _event(
        "columbina.traveler_trusts", "columbina", "主角信任她",
        goal_relevance=0.9, goal_conduciveness=0.7,
        expectedness=0.2, other_agency=0.9,
        coping_potential=0.3, social_evaluation=0.8,
    ),
    _event(
        "columbina.inherit_power", "columbina", "继承月神权能",
        goal_relevance=1.0, goal_conduciveness=0.6,
        expectedness=0.1, other_agency=0.4,
        coping_potential=0.7, social_evaluation=0.0,
    ),
    _event(
        "columbina.followers_demand", "columbina", "信徒无止境索取",
        goal_relevance=0.7, goal_conduciveness=-0.4,
        expectedness=0.9, other_agency=1.0,
        coping_potential=0.3, social_evaluation=-0.2,
    ),
    _event(
        "columbina.betrayed", "columbina", "被背叛/利用",
        goal_relevance=0.9, goal_conduciveness=-0.8,
        expectedness=0.6, other_agency=0.9,
        coping_potential=0.5, social_evaluation=-0.6,
    ),
    _event(
        "columbina.imprisoned", "columbina", "被困/囚禁",
        goal_relevance=1.0, goal_conduciveness=-0.6,
        expectedness=0.4, other_agency=0.8,
        coping_potential=0.6, social_evaluation=0.0,
    ),
    _event(
        "columbina.facing_dissolution", "columbina", "面临消亡",
        goal_relevance=1.0, goal_conduciveness=-0.9,
        expectedness=0.3, other_agency=0.5,
        coping_potential=0.4, social_evaluation=0.0,
    ),
    _event(
        "columbina.hurt_trusted", "columbina", "伤害了信任她的人",
        goal_relevance=0.9, goal_conduciveness=-0.6,
        expectedness=0.5, other_agency=0.2,
        coping_potential=0.3, social_evaluation=-0.4,
    ),
    _event(
        "columbina.public_gaze", "columbina", "在公共场合被注视",
        goal_relevance=0.3, goal_conduciveness=-0.1,
        expectedness=0.5, other_agency=0.5,
        coping_potential=0.7, social_evaluation=0.0,
    ),
    _event(
        "columbina.singing", "columbina", "唱歌",
        goal_relevance=0.5, goal_conduciveness=0.3,
        expectedness=0.8, other_agency=0.0,
        coping_potential=0.6, social_evaluation=0.0,
    ),

    # ── engine.py 演示事件 ───────────────────────────────────
    _event(
        "engine.praised_by_friend", "engine-demo", "被朋友夸奖",
        goal_relevance=0.7, goal_conduciveness=0.8,
        expectedness=0.3, other_agency=0.8,
        coping_potential=0.5, social_evaluation=0.6,
    ),
    _event(
        "engine.stood_up", "engine-demo", "被放鸽子（别人的责任）",
        goal_relevance=0.8, goal_conduciveness=-0.7,
        expectedness=0.2, other_agency=0.9,
        coping_potential=0.2, social_evaluation=0.0,
    ),
    _event(
        "engine.hurt_friend", "engine-demo", "我对朋友说了伤人的话",
        goal_relevance=0.8, goal_conduciveness=-0.6,
        expectedness=0.3, other_agency=0.3,
        coping_potential=0.5, social_evaluation=-0.5,
        note="高 self_agency；L1 只观察六通道，guilt 留给 L2。",
    ),

    # ── L1 合成边界事件 ──────────────────────────────────────
    _event(
        "synthetic.routine_notice", "synthetic", "例行通知（低相关·完全预期）",
        goal_relevance=0.1, goal_conduciveness=0.0,
        expectedness=1.0, other_agency=0.0,
        coping_potential=0.8, social_evaluation=0.0,
        note="接近基线的弱事件。",
    ),
    _event(
        "synthetic.surprising_neutral", "synthetic", "完全意外的中性事件",
        goal_relevance=0.5, goal_conduciveness=0.0,
        expectedness=0.0, other_agency=0.2,
        coping_potential=0.5, social_evaluation=0.0,
        note="结果中性，但意外本身应有 surprise。",
    ),
    _event(
        "synthetic.expected_good", "synthetic", "预期内的好事",
        goal_relevance=0.8, goal_conduciveness=0.6,
        expectedness=1.0, other_agency=0.2,
        coping_potential=0.7, social_evaluation=0.5,
        note="高预期 → surprise 应为 0。",
    ),
    _event(
        "synthetic.unexpected_blow", "synthetic", "完全意外的重大打击",
        goal_relevance=1.0, goal_conduciveness=-1.0,
        expectedness=0.0, other_agency=0.8,
        coping_potential=0.0, social_evaluation=-0.8,
        note="极端负面 + 极端意外，用于冲击与不变量上界。",
    ),
    _event(
        "synthetic.low_coping_external", "synthetic", "低应对·外部归因",
        goal_relevance=0.9, goal_conduciveness=-0.7,
        expectedness=0.5, other_agency=1.0,
        coping_potential=0.1, social_evaluation=-0.3,
    ),
    _event(
        "synthetic.low_coping_internal", "synthetic", "低应对·内部归因",
        goal_relevance=0.9, goal_conduciveness=-0.7,
        expectedness=0.5, other_agency=0.0,
        coping_potential=0.1, social_evaluation=-0.3,
        note="与上一事件配对；anger 应低于外部归因，guilt 留给 L2。",
    ),
    _event(
        "synthetic.high_coping_external", "synthetic", "高应对·外部归因",
        goal_relevance=0.9, goal_conduciveness=-0.7,
        expectedness=0.5, other_agency=1.0,
        coping_potential=0.9, social_evaluation=-0.3,
        note="与低应对配对；fear / anger 应低于低应对。",
    ),
    _event(
        "synthetic.pure_negative_zero_cues", "synthetic", "纯负面·零应对·无正面线索",
        goal_relevance=1.0, goal_conduciveness=-0.8,
        expectedness=0.5, other_agency=0.5,
        coping_potential=0.0, social_evaluation=0.0,
        note="joy 原始激活应为 0：没有正面结果、应对或社交线索。",
    ),
    _event(
        "synthetic.social_praise", "synthetic", "社交赞誉（中性结果）",
        goal_relevance=0.6, goal_conduciveness=0.0,
        expectedness=0.5, other_agency=0.5,
        coping_potential=0.5, social_evaluation=0.8,
        note="结果中性，仅社交评价为正 → 少量 joy。",
    ),
    _event(
        "synthetic.social_humiliation", "synthetic", "社交羞辱（中性结果）",
        goal_relevance=0.8, goal_conduciveness=0.0,
        expectedness=0.5, other_agency=0.7,
        coping_potential=0.5, social_evaluation=-0.9,
        note="结果中性，仅社交评价为负 → disgust 应显著。",
    ),
    _event(
        "synthetic.tiny_miss", "synthetic", "微小挫折",
        goal_relevance=0.1, goal_conduciveness=-0.1,
        expectedness=0.5, other_agency=0.5,
        coping_potential=0.5, social_evaluation=0.0,
        note="低相关小事件，所有通道应保持很小。",
    ),
    _event(
        "synthetic.complete_success", "synthetic", "完全成功（高相关·高控制）",
        goal_relevance=1.0, goal_conduciveness=1.0,
        expectedness=0.5, other_agency=0.0,
        coping_potential=1.0, social_evaluation=0.8,
        note="joy 的上界样本；surprise 仍来自 50% 意外。",
    ),
)


EVENT_BY_KEY: Dict[str, L1Event] = {event.key: event for event in EVENTS}


def event_by_key(key: str) -> L1Event:
    return EVENT_BY_KEY[key]


def events_by_source(source: str) -> Tuple[L1Event, ...]:
    return tuple(event for event in EVENTS if event.source == source)
