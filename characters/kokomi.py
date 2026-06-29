"""
珊瑚宫心海 — 角色蒸馏
来源: https://zh.wikipedia.org/wiki/珊瑚宫心海
引擎: emotion-engine v0.1

蒸馏逻辑:
  wiki文字 → OCEAN人格 → 情绪基线 → 触发映射 → 测试场景
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import (
    EmotionEngine, EmotionalState, Personality, Appraisal, Channel,
    MemoryStore, SensitizationStore, FAST_CHANNELS
)


# ═══════════════════════════════════════════════
# 蒸馏层: wiki → OCEAN
# ═══════════════════════════════════════════════

kokomi_personality = Personality(
    openness           = 0.7,   # 博览群书，痴迷兵法和知识
    conscientiousness  = 0.9,   # 年少登位，井井有条，责任感极重
    extraversion       = 0.15,  # 极度内向——演讲手抖，最大快乐是独处
    agreeableness      = 0.75,  # 辅助治疗者，民众爱戴，对旅行者温柔
    neuroticism        = 0.8,   # 如履薄冰，承受巨大压力，不自觉手抖
)


# ═══════════════════════════════════════════════
# 蒸馏层: wiki → 触发映射
# ═══════════════════════════════════════════════

def kokomi_appraise(event_type: str) -> Appraisal:
    """
    同一个事件类型，心海和其他人会评估出不同结果。
    这是蒸馏层的核心——不是事件本身，是"对她来说这意味着什么"。
    """

    triggers = {
        # ===== 正面事件 =====
        "独处读书": Appraisal(
            goal_relevance=0.9, goal_conduciveness=0.8,
            expectedness=1.0,   # 日常——很习惯
            other_agency=0.0,   # 纯自己的事
            coping_potential=0.95,
            social_evaluation=0.0,
        ),
        "被信任的人夸奖": Appraisal(
            goal_relevance=0.7, goal_conduciveness=0.6,
            expectedness=0.3,   # 意外——她不觉得自己值得夸
            other_agency=0.8,   # 别人的评价
            coping_potential=0.3,  # 不太会应对夸奖
            social_evaluation=0.8,
        ),
        "帮助了民众": Appraisal(
            goal_relevance=0.9, goal_conduciveness=0.9,
            expectedness=0.7,
            other_agency=0.3,   # 她主导
            coping_potential=0.8,
            social_evaluation=0.7,
        ),

        # ===== 负面事件 =====
        "公开演讲": Appraisal(
            goal_relevance=0.9, goal_conduciveness=-0.3,  # 挡住她了——社交是障碍
            expectedness=1.0,    # 每次都是煎熬但每次都得做
            other_agency=0.1,    # 自己必须面对
            coping_potential=0.2,  # "不自觉手抖"——控制不了
            social_evaluation=0.8,  # 别人盯着她看
        ),
        "部下牺牲": Appraisal(
            goal_relevance=1.0, goal_conduciveness=-0.9,  # 严重阻碍目标
            expectedness=0.5,
            other_agency=0.6,    # 有敌人的因素
            coping_potential=0.1,  # "对问题察觉迟缓"——她认为是自己的疏忽
            social_evaluation=-0.5,  # 她觉得自己被批评了
        ),
        "被批评领导能力": Appraisal(
            goal_relevance=0.9, goal_conduciveness=-0.7,
            expectedness=0.6,    # 一直在等这一天
            other_agency=1.0,    # 纯粹他人在评判
            coping_potential=0.2,
            social_evaluation=-0.7,
        ),
        "不熟悉的社交场合": Appraisal(
            goal_relevance=0.6, goal_conduciveness=-0.2,
            expectedness=0.4,    # 不知道会发生什么
            other_agency=0.5,
            coping_potential=0.1,  # "社交相当贫乏"——完全没招
            social_evaluation=0.3,
        ),

        # ===== 中性/混合事件 =====
        "制定作战计划": Appraisal(
            goal_relevance=1.0, goal_conduciveness=0.5,  # 做擅长的事但背负压力
            expectedness=0.9,
            other_agency=0.0,
            coping_potential=0.8,  # 军事才能——高控制感
            social_evaluation=0.5,
        ),
        "和旅行者独处": Appraisal(
            goal_relevance=0.6, goal_conduciveness=0.4,
            expectedness=0.5,
            other_agency=0.4,
            coping_potential=0.5,  # 比其他人放松但仍紧张
            social_evaluation=0.4,
        ),
    }

    return triggers.get(event_type, Appraisal())


# ═══════════════════════════════════════════════
# 创建心海引擎实例
# ═══════════════════════════════════════════════

def create_kokomi():
    """创建一个心海的情绪引擎"""
    state = EmotionalState()
    bl = kokomi_personality.baseline()
    for ch, val in bl.items():
        setattr(state, ch.value, val)
    return EmotionEngine(state=state, personality=kokomi_personality, memory=MemoryStore(), scars=SensitizationStore())


# ═══════════════════════════════════════════════
# 测试场景
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import time

    kokomi = create_kokomi()

    print("=" * 50)
    print("  Kokomi Emotion Engine Test")
    print("=" * 50)

    def show(label, result):
        print(f"\n-- {label} --")
        s = result["state"]
        print(f"  joy={s['joy']:.2f} sad={s['sadness']:.2f} anger={s['anger']:.2f} fear={s['fear']:.2f}")
        print(f"  love={s['love']:.2f} trust={s['trust']:.2f} longing={s.get('longing',0):.2f} guilt={s.get('guilt',0):.2f}")
        if result["blends"]:
            for b in result["blends"]:
                label = {"joy+sadness": "[bittersweet]", "love+fear": "[fear of loss]"}.get(b, f"[{b}]")
                print(f"  {label}")
        if result.get("shock_channels"):
            print(f"  !! shock: {result['shock_channels']}")
        if "memory" in result:
            print(f"  mem: f={result['memory']['flash_count']} l={result['memory']['long_term_count']} s={result['memory']['short_term_count']} p={result['memory']['pending_count']}")
        if "atmosphere" in result:
            print(f"  atmos: {result['atmosphere']}")

    def wait(minutes):
        kokomi.state._last_update -= minutes * 60

    # 初始状态
    show("Baseline", {"state": kokomi.state.to_dict(), "blends": [], "shock_channels": []})

    # 场景1: 正常工作日——制定作战计划
    show("Planning battle", kokomi.tick(kokomi_appraise("制定作战计划")))
    wait(30)
    show("+30min decay", kokomi.tick())

    # 场景2: 公开演讲（压力）
    show("Public speech", kokomi.tick(kokomi_appraise("公开演讲")))
    wait(120)
    show("+2hr decay", kokomi.tick())

    # 场景3: 和旅行者独处
    show("With Traveler", kokomi.tick(kokomi_appraise("和旅行者独处")))

    # 场景4: 部下牺牲（别人造成的损失）→ social_hurt scar
    show("Soldier lost #1", kokomi.tick(kokomi_appraise("部下牺牲")))
    wait(180)
    show("+3hr after loss", kokomi.tick())

    # 场景5: 独自读书恢复
    show("Reading alone", kokomi.tick(kokomi_appraise("独处读书")))

    # === v0.4 新功能测试 ===

    # 测试 longing: 模拟离线 24 小时后唤醒
    print("\n=== v0.4: longing + sensitization ===")
    wait(1440)  # 24 小时离线
    wake_result = kokomi.wake()
    print(f"  offline: {wake_result['offline_minutes']:.0f}min")
    print(f"  longing after 24h away (love={kokomi.state.love:.2f}): {kokomi.state.longing:.3f}")
    print(f"  compressed fast: anger={wake_result['compressed_fast']['anger']:.0f}eq_min fear={wake_result['compressed_fast']['fear']:.0f}eq_min")

    # 测试 sensitization: 重复"部下牺牲" 3 次
    show("Soldier lost #2", kokomi.tick(kokomi_appraise("部下牺牲")))
    wait(60)
    show("Soldier lost #3", kokomi.tick(kokomi_appraise("部下牺牲")))
    wait(60)
    show("Soldier lost #4 (scar)", kokomi.tick(kokomi_appraise("部下牺牲")))
    # 第4次应该触发 kindling——old scar amplifies sad/fear/anger
    scars = kokomi.scars.all_scars()
    if scars:
        print(f"  scars: {scars}")

    # 测试 guilt: 心海意识到自己的决策失误导致部下牺牲
    show("Guilt: my fault", kokomi.tick(Appraisal(
        goal_relevance=1.0, goal_conduciveness=-0.7,
        expectedness=0.3, other_agency=0.3,  # self_agency=0.7
        coping_potential=0.2, social_evaluation=-0.5)))
