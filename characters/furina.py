"""
芙宁娜 (Furina de Fontaine) — 角色蒸馏
来源: https://zh.wikipedia.org/wiki/芙寧娜
引擎: emotion-engine v0.5

蒸馏逻辑:
  wiki → two personas → OCEAN(inner) → baseline → appraisal trigger → 测试

核心挑战:
  外层: 浮夸自信、戏剧化、"水神"人设——但这是表演
  内层: 疲惫孤独、胆小透明、500年独自忍受——这才是本人
  500年的面具从未摘下。这是引擎的终极测试: 人格不变，面具在叠。
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import (
    EmotionEngine, EmotionalState, Personality, Appraisal, Channel,
    MemoryStore, SensitizationStore, FAST_CHANNELS
)


# ═══════════════════════════════════════════════
# 蒸馏: wiki → OCEAN (内层——真实人格)
# ═══════════════════════════════════════════════
# 外层是表演，OCEAN 应该蒸馏她的真实内核:
#   极高尽责——500年从不放松警惕
#   高神经质——内心透明、容易看穿、焦虑
#   低外向——表演让她疲惫，独处才是解脱
#   高宜人——为他人牺牲一切

furina_personality = Personality(
    openness           = 0.6,   # 戏剧化、创意，但不是极端
    conscientiousness  = 0.85,  # 500年从不放松警惕——极端的自我控制
    extraversion       = 0.25,  # 真正的她是内向的——"厌倦表演"
    agreeableness      = 0.70,  # 为枫丹牺牲一切，暗中拯救国家
    neuroticism        = 0.85,  # "透明且容易被看穿"、焦虑、脆弱
)


# ═══════════════════════════════════════════════
# 蒸馏: wiki → 触发映射
# ═══════════════════════════════════════════════
# 独特之处: "公开表演"对她是负面的（疲惫但必须做）
# 而"独处"是正面的（终于不用演了）

def furina_appraise(event_type: str) -> Appraisal:
    triggers = {
        # ===== 正面 =====
        "独处——不用演了": Appraisal(
            goal_relevance=0.9, goal_conduciveness=0.7,
            expectedness=1.0,    # 500年了，只有独处是常态
            other_agency=0.0,
            coping_potential=0.9,
            social_evaluation=0.0,
        ),
        "被真正理解": Appraisal(
            goal_relevance=1.0, goal_conduciveness=0.8,
            expectedness=0.1,    # 极度意外——500年没人看穿
            other_agency=0.9,    # 来自别人的理解
            coping_potential=0.2,  # 不知道怎么反应——被看穿了反而慌
            social_evaluation=0.9,
        ),
        "不再需要表演": Appraisal(
            goal_relevance=1.0, goal_conduciveness=1.0,  # 纯粹的解脱
            expectedness=0.05,   # 几乎不敢想象
            other_agency=0.6,    # 一半是别人的允许，一半是自己放下
            coping_potential=0.3,
            social_evaluation=0.5,
        ),
        "帮助了剧团（真心投入）": Appraisal(
            goal_relevance=0.7, goal_conduciveness=0.6,
            expectedness=0.6,
            other_agency=0.3,
            coping_potential=0.7,  # 做回了自己擅长的事
            social_evaluation=0.6,
        ),

        # ===== 负面 =====
        "被要求公开表演（水神角色）": Appraisal(
            goal_relevance=0.9, goal_conduciveness=-0.3,  # 疲惫——但必须做
            expectedness=1.0,    # 完全预期——500年如一日
            other_agency=0.7,    # 被臣民/天理逼着
            coping_potential=0.4,  # 能应付但消耗巨大
            social_evaluation=0.9,  # 所有人都在看她
        ),
        "被质疑/审判": Appraisal(
            goal_relevance=1.0, goal_conduciveness=-0.7,
            expectedness=0.3,    # 预感过但不愿面对
            other_agency=1.0,    # 完全被人审判
            coping_potential=0.1,  # 无法自救——需要别人来救
            social_evaluation=-0.8,
        ),
        "秘密可能被揭穿": Appraisal(
            goal_relevance=1.0, goal_conduciveness=-0.6,
            expectedness=0.6,    # "迟早会来的"
            other_agency=0.5,
            coping_potential=0.2,  # 没有实质权力
            social_evaluation=-0.5,
        ),
        "被盯着看": Appraisal(
            goal_relevance=0.5, goal_conduciveness=-0.2,
            expectedness=1.0,
            other_agency=1.0,    # 别人的目光
            coping_potential=0.4,  # 习惯了但不喜欢
            social_evaluation=0.3,
        ),
        "500年的孤独回忆涌上来": Appraisal(
            goal_relevance=1.0, goal_conduciveness=-0.4,
            expectedness=0.5,
            other_agency=0.0,    # 不是谁害的——这就是她的命运
            coping_potential=0.3,
            social_evaluation=0.0,
        ),

        # ===== 表演模式（外层）=====
        "切换为表演模式": Appraisal(
            goal_relevance=0.8, goal_conduciveness=0.3,  # 表面正面——维持人设
            expectedness=1.0,     # 500年的肌肉记忆
            other_agency=0.1,
            coping_potential=0.8,  # 极度擅长
            social_evaluation=0.8,  # 臣民的崇拜
        ),
        "表演后独处（面具摘下的瞬间）": Appraisal(
            goal_relevance=0.9, goal_conduciveness=0.5,  # 混合——解脱但有残留
            expectedness=1.0,
            other_agency=0.0,
            coping_potential=0.7,
            social_evaluation=0.0,
        ),
    }

    return triggers.get(event_type, Appraisal())


# ═══════════════════════════════════════════════
# 创建芙宁娜引擎实例
# ═══════════════════════════════════════════════

def create_furina():
    state = EmotionalState()
    bl = furina_personality.baseline()
    for ch, val in bl.items():
        setattr(state, ch.value, val)
    return EmotionEngine(state=state, personality=furina_personality,
                         memory=MemoryStore(), scars=SensitizationStore())


# ═══════════════════════════════════════════════
# 测试场景 — 双面人生
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import time

    f = create_furina()

    print("=" * 55)
    print("  Furina — 500 Years Behind the Mask")
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
        if "atmosphere" in result:
            print(f"  atmos: {result['atmosphere']}")

    def wait(minutes):
        f.state._last_update -= minutes * 60

    # 初始基线
    show("Baseline (true self)", {"state": f.state.to_dict(), "blends": [], "shock_channels": []})

    # 场景1: 被要求公开表演——不得不切换人设
    show("Forced to perform", f.tick(furina_appraise("被要求公开表演（水神角色）")))
    wait(10)
    show("Performance mode ON", f.tick(furina_appraise("切换为表演模式")))

    # 场景2: 表演后独处——面具摘下
    wait(120)
    show("Alone after show", f.tick(furina_appraise("表演后独处（面具摘下的瞬间）")))

    # 场景3: 被盯着看——厌恶但习惯
    show("Stared at", f.tick(furina_appraise("被盯着看")))

    # 场景4: 秘密可能被揭穿——恐惧+孤独
    show("Secret exposed?", f.tick(furina_appraise("秘密可能被揭穿")))

    # 场景5: 被审判——崩溃
    show("ON TRIAL", f.tick(furina_appraise("被质疑/审判")))

    # 场景6: 被真正理解——500年来第一次
    wait(180)
    show("Truly understood", f.tick(furina_appraise("被真正理解")))

    # 场景7: 终于不用演了——解脱
    show("FREEDOM", f.tick(furina_appraise("不再需要表演")))

    # 离线思念测试
    print("\n=== longing: the weight of 500 years ===")
    wait(7 * 24 * 60)  # 象征性 7 天（500年的回响）
    wake_result = f.wake()
    print(f"  offline (simulated): {wake_result['offline_minutes']:.0f} min")
    print(f"  longing after eons alone: {f.state.longing:.3f}")
    print(f"  [500 years of isolation — the fountain whispers: '好漫长...好孤独...还要多久...']")

    print("\n=== Furina: The Mask vs The Self ===")
    ks = f.state.to_dict()
    print(f"  joy={ks['joy']:.2f} sad={ks['sadness']:.2f} fear={ks['fear']:.2f} trust={ks['trust']:.2f}")
    print(f"  The performance is over. She is finally just herself.")
