"""
哥伦比娅 (Columbina) — 角色蒸馏
来源: https://zh.wikipedia.org/wiki/哥倫比婭
引擎: emotion-engine v0.4

蒸馏逻辑:
  wiki → OCEAN → baseline → appraisal trigger → 测试场景

与心海形成对照:
  心海 = 高尽责·高焦虑·公开演讲手抖的军师
  哥伦比娅 = 低尽责·极内向·孤独清冷的月神
  两个角色在 trust baseline、joy baseline、社交应对上完全相反
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import (
    EmotionEngine, EmotionalState, Personality, Appraisal, Channel,
    MemoryStore, SensitizationStore, FAST_CHANNELS
)

columbina_name = "哥伦比娅"


# ═══════════════════════════════════════════════
# 蒸馏层: wiki → OCEAN
# ═══════════════════════════════════════════════

columbina_personality = Personality(
    openness           = 0.9,   # 月神——连接宇宙、时间回溯、跨维度存在
    conscientiousness  = 0.25,  # 恍惚昏睡、离开信徒、不管理不规划
    extraversion       = 0.1,   # 极内向——"难以与人正常对话的语速"、大量气音
    agreeableness      = 0.35,  # 会离开厌烦的信徒，但也会为保护伙伴自我牺牲
    neuroticism        = 0.45,  # 有孤独与虚无，但不是焦虑——是月光的冷
)


# ═══════════════════════════════════════════════
# 蒸馏层: wiki → 触发映射
# ═══════════════════════════════════════════════

def columbina_appraise(event_type: str) -> Appraisal:
    """
    同一个事件——哥伦比娅的认知评估和心海完全不同。
    她不在乎效率、不在乎别人评价、不在乎社交。
    她在乎: 自由、被需要的感觉、与主角的联结。
    """

    triggers = {
        # ===== 正面事件 =====
        "独处": Appraisal(
            goal_relevance=0.6, goal_conduciveness=0.7,
            expectedness=1.0,    # 很习惯——孤独是常态
            other_agency=0.0,
            coping_potential=0.9,
            social_evaluation=0.0,
        ),
        "和主角在一起": Appraisal(
            goal_relevance=0.8, goal_conduciveness=0.5,
            expectedness=0.3,    # 意外——不习惯有人愿意靠近她
            other_agency=0.5,
            coping_potential=0.4,  # 不太会处理亲密关系
            social_evaluation=0.5,
        ),
        "主角信任她": Appraisal(
            goal_relevance=0.9, goal_conduciveness=0.7,
            expectedness=0.2,    # 意外——"第一次有人不把我当月神"
            other_agency=0.9,    # 完全来自主角
            coping_potential=0.3,  # 被信任让她不知所措
            social_evaluation=0.8,
        ),
        "继承月神权能": Appraisal(
            goal_relevance=1.0, goal_conduciveness=0.6,
            expectedness=0.1,    # 极度意外
            other_agency=0.4,    # 初代月神赐予
            coping_potential=0.7,
            social_evaluation=0.0,
        ),

        # ===== 负面事件 =====
        "信徒无止境索取": Appraisal(
            goal_relevance=0.7, goal_conduciveness=-0.4,
            expectedness=0.9,    # 预料之中——习惯了
            other_agency=1.0,    # 完全是别人的要求
            coping_potential=0.3,  # 无法满足——只能离开
            social_evaluation=-0.2,
        ),
        "被背叛/利用": Appraisal(
            goal_relevance=0.9, goal_conduciveness=-0.8,
            expectedness=0.6,    # 半意外——冰神的追捕不是完全没料到
            other_agency=0.9,    # 多托雷/愚人众的责任
            coping_potential=0.5,
            social_evaluation=-0.6,
        ),
        "被困/囚禁": Appraisal(
            goal_relevance=1.0, goal_conduciveness=-0.6,
            expectedness=0.4,    # 意外——没料到陷阱
            other_agency=0.8,
            coping_potential=0.6,  # 冷静探索，利用漏洞
            social_evaluation=0.0,
        ),
        "面临消亡": Appraisal(
            goal_relevance=1.0, goal_conduciveness=-0.9,
            expectedness=0.3,
            other_agency=0.5,    # 一部分是敌人，一部分是自己选择
            coping_potential=0.4,  # 化为月光——不是无力，是选择
            social_evaluation=0.0,
        ),
        "伤害了信任她的人": Appraisal(
            goal_relevance=0.9, goal_conduciveness=-0.6,
            expectedness=0.5,
            other_agency=0.2,    # self_agency=0.8 → 愧疚高
            coping_potential=0.3,
            social_evaluation=-0.4,
        ),

        # ===== 中性/混合事件 =====
        "在公共场合被注视": Appraisal(
            goal_relevance=0.3, goal_conduciveness=-0.1,
            expectedness=0.5,
            other_agency=0.5,
            coping_potential=0.7,  # 习惯了——闭眼不理会
            social_evaluation=0.0,  # 不在乎
        ),
        "唱歌": Appraisal(
            goal_relevance=0.5, goal_conduciveness=0.3,  # 苦乐参半
            expectedness=0.8,
            other_agency=0.0,
            coping_potential=0.6,
            social_evaluation=0.0,
        ),
    }

    return triggers.get(event_type, Appraisal())


# ═══════════════════════════════════════════════
# 创建哥伦比娅引擎实例
# ═══════════════════════════════════════════════

def create_columbina():
    state = EmotionalState()
    bl = columbina_personality.baseline()
    for ch, val in bl.items():
        setattr(state, ch.value, val)
    return EmotionEngine(state=state, personality=columbina_personality,
                         memory=MemoryStore(), scars=SensitizationStore())


# ═══════════════════════════════════════════════
# 测试场景 — 对照心海
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import time

    c = create_columbina()

    print("=" * 55)
    print("  Columbina Emotion Engine Test (vs Kokomi)")
    print("=" * 55)

    def show(label, result):
        print(f"\n-- {label} --")
        s = result["state"]
        print(f"  joy={s['joy']:.2f} sad={s['sadness']:.2f} anger={s['anger']:.2f} fear={s['fear']:.2f}")
        print(f"  love={s['love']:.2f} trust={s['trust']:.2f} longing={s.get('longing',0):.2f} guilt={s.get('guilt',0):.2f}")
        if result.get("blends"):
            for b in result["blends"]:
                label = {"joy+sadness": "[bittersweet]", "love+fear": "[fear of loss]"}.get(b, f"[{b}]")
                print(f"  {label}")
        if result.get("shock_channels"):
            print(f"  !! shock: {result['shock_channels']}")
        if "memory" in result:
            print(f"  mem: f={result['memory']['flash_count']} l={result['memory']['long_term_count']} s={result['memory']['short_term_count']}")
        if "atmosphere" in result:
            print(f"  atmos: {result['atmosphere']}")

    def wait(minutes):
        c.state._last_update -= minutes * 60

    # 初始基线
    show("Baseline", {"state": c.state.to_dict(), "blends": [], "shock_channels": []})
    print("  [Kokomi: joy=0.24 sad=0.17 trust=0.22 fear=0.15]")
    s0 = c.state.to_dict()
    print(f"  [Columbina: joy={s0['joy']:.2f} sad={s0['sadness']:.2f} trust={s0['trust']:.2f} fear={s0['fear']:.2f}]")

    # 场景1: 独处——她的舒适区
    show("Alone (comfort zone)", c.tick(columbina_appraise("独处")))
    wait(60)

    # 场景2: 信徒索取——厌倦但不太痛
    show("Followers demanding", c.tick(columbina_appraise("信徒无止境索取")))

    # 场景3: 遇到主角——信任开始萌芽
    show("Meeting Traveler", c.tick(columbina_appraise("和主角在一起")))
    wait(120)
    show("+2hr later", c.tick())

    # 场景4: 主角信任她——从未有过的体验
    show("Traveler trusts her", c.tick(columbina_appraise("主角信任她")))

    # 场景5: 被背叛——信任本就低，不会腰斩但会痛
    show("Betrayed (by Fatui)", c.tick(columbina_appraise("被背叛/利用")))

    # 场景6: 伤害了主角（自我归责）
    show("Hurt Traveler (guilt)", c.tick(columbina_appraise("伤害了信任她的人")))

    # 离线思念测试
    print("\n=== longing test ===")
    wait(2880)  # 2 天离线
    wake_result = c.wake()
    print(f"  offline: {wake_result['offline_minutes']:.0f}min")
    print(f"  longing after 2 days: {c.state.longing:.3f}")
    print(f"  [Kokomi after 1 day: longing=0.059 — Columbina attachment builds differently]")

    # 对比总结
    print("\n=== Key Differences vs Kokomi ===")
    ks = c.state.to_dict()
    print(f"  trust:  Kokomi=0.22 → Columbina={ks['trust']:.2f}")
    print(f"  sadness: Kokomi=0.17 → Columbina={ks['sadness']:.2f}")
    print(f"  guilt after self-blame: {ks['guilt']:.2f}")
    print(f"  longing after 2 days away: {c.state.longing:.3f}")
    scars = c.scars.all_scars()
    if scars:
        print(f"  scars: {scars}")
