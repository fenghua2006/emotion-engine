"""表达层：把情绪状态翻译成台词、行为、Live2D 参数与 LLM prompt。

这一层不参与情绪动力学计算。它只读取 ``EmotionalState`` / ``ToleranceState``，
输出可展示的结果；修改表达风格时不应改动 ``core.py`` 或 ``engine.tick()``。

``engine.py`` 仍 re-export 这些名字，保持旧 import 路径兼容。
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from core import Channel, EmotionalState
from tolerance import ZONE_HYPER, ZONE_HYPO


# 行为倾向映射
def _action_tendency(state: "EmotionalState", atmos: float,
                     zone: str = "regulated") -> str:
    """从情绪状态推导行为倾向——趋近 or 回避 or 防御。

    L5.1：hypo 是关机而非“更强烈的回避”。趋近和回避都被压低，
    方向判定也更钝，因此同等强度的冲突更容易落到 still。
    """
    approach = state.joy + state.trust + state.love
    avoid    = state.fear + state.sadness + state.disgust
    dominance_ratio = 1.2
    if atmos > 0.3:
        avoid *= 1.3  # 紧绷氛围放大回避
    if zone == ZONE_HYPER:
        approach *= 0.85  # 过载时战/逃，趋近冲动下降
        avoid *= 1.25
    elif zone == ZONE_HYPO:
        approach *= 0.60  # 关机：趋近与回避都迟缓
        avoid *= 0.55
        dominance_ratio = 1.35
    if approach > avoid * dominance_ratio:
        return "approach"   # 趋近——想靠近
    elif avoid > approach * dominance_ratio:
        return "withdraw"   # 回避——想躲
    else:
        return "still"      # 僵住——不确定


# 回应模板池（按角色 × 主导情绪）
# 每个角色有自己的声音。Kokomi 战略家语气，Columbina 朦胧稀疏，Furina 戏剧化。
_CHARACTER_POOLS = {
    "kokomi": {
        "joy_dominant": [
            "太好了。", "不胜荣幸。", "这份战果……值得铭记。"
        ],
        "sadness_dominant": [
            "无妨。", "只是……又想起了一些事。", "不必担心我。"
        ],
        "anger_dominant": [
            "这是战术失误。", "我不会让部下白白牺牲。", "请把情报再说一遍。"
        ],
        "fear_dominant": [
            "我……", "能给我一点时间吗。", "人太多了……"
        ],
        "surprise_dominant": [
            "你说什么？", "不在计划之内……", "等、等一下——"
        ],
        "longing_dominant": [
            "你终于来了。", "我还以为这次……你不会回来了。", "……那边的情况怎么样？"
        ],
        "guilt_dominant": [
            "是我的责任。", "身为指挥官……不该有这种失误。", "你不该原谅我的。"
        ],
        "neutral": [
            "嗯。", "继续。", "我在听。"
        ],
    },
    "columbina": {
        "joy_dominant": [
            "……嗯。", "……暖暖的。", "第一次有人……这样看我。"
        ],
        "sadness_dominant": [
            "……", "月光……好冷。", "不。只是……习惯了。"
        ],
        "anger_dominant": [
            "……走开。", "别碰我。", "你不是他。"
        ],
        "fear_dominant": [
            "……别盯着我看。", "太快了……", "我不能……"
        ],
        "surprise_dominant": [
            "诶？", "……什么？", "你……在跟我说话？"
        ],
        "longing_dominant": [
            "你……回来了。", "我还以为你不会再来了。", "……上次你说的话，我一直在想。"
        ],
        "guilt_dominant": [
            "是我……害了你。", "我不该……", "对不起……"
        ],
        "neutral": [
            "……", "嗯。", "……说吧。"
        ],
    },
    "furina": {
        "joy_dominant": [
            "Bravo！太完美了！", "不愧是我~", "今天是个值得庆祝的日子！"
        ],
        "sadness_dominant": [
            "……没什么，只是有点累了。", "别看我。", "好漫长……"
        ],
        "anger_dominant": [
            "够了！", "你以为你在跟谁说话？！", "……不。当我没说。"
        ],
        "fear_dominant": [
            "等一下……", "不要盯着我看……", "我不是……你不明白……"
        ],
        "surprise_dominant": [
            "什——什么？！", "不可能！", "你……你刚才说什么？"
        ],
        "longing_dominant": [
            "你来了。", "我还以为你不会来了……", "上一次有人这样等我……是500年前的事了。"
        ],
        "guilt_dominant": [
            "是我……让所有人失望了。", "我演不下去了……", "对不起……让你看到这样的我。"
        ],
        "neutral": [
            "嗯哼~", "什么事？", "说来听听。"
        ],
    },
    "default": {
        "joy_dominant": ["好。", "嗯。", "谢谢。"],
        "sadness_dominant": ["……", "没什么。", "不用管我。"],
        "anger_dominant": ["够了。", "别说了。", "……"],
        "fear_dominant": ["等一下……", "我有点……", "不是你想的那样。"],
        "surprise_dominant": ["诶？", "什么？", "真的吗？"],
        "longing_dominant": ["你来了。", "好久不见。", "我还以为你忘了。"],
        "guilt_dominant": ["是我不好。", "对不起。", "你不该原谅我的。"],
        "neutral": ["嗯。", "……", "好。"],
    },
}

# L5.1：超出耐受窗后，局部模板必须和 zone 一致，不能继续用平静时
# 按主导情绪挑出的台词。hyper 是战/逃，hypo 是关机，不是“更平静”。
_ZONE_POOLS = {
    "kokomi": {
        "hyper": [
            "现在不适合继续谈。", "请退后。", "我需要重新判断局势。"
        ],
        "hypo": [
            "……先到这里。", "我需要一点时间。", "……我听不清了。"
        ],
    },
    "columbina": {
        "hyper": [
            "……别过来。", "远离我。", "……安静。"
        ],
        "hypo": [
            "……", "让我安静。", "……好累。"
        ],
    },
    "furina": {
        "hyper": [
            "别逼我。", "……我说了，够了。", "你还想怎样？！"
        ],
        "hypo": [
            "……让我歇一会儿。", "我演不动了。", "……随便吧。"
        ],
    },
    "default": {
        "hyper": [
            "别说了。", "让我一个人待着。", "我现在不想谈。"
        ],
        "hypo": [
            "……", "让我静静。", "我现在不想说。"
        ],
    },
}


def _select_local_utterance(character_id: str, dominant: str, zone: str) -> str:
    """按 zone 优先选择本地台词；只有 regulated 才按主导情绪挑。"""
    pool = _CHARACTER_POOLS.get(character_id, _CHARACTER_POOLS["default"])
    if zone in (ZONE_HYPER, ZONE_HYPO):
        zone_pool = _ZONE_POOLS.get(character_id, _ZONE_POOLS["default"])
        return random.choice(zone_pool[zone])
    return random.choice(pool.get(dominant, pool["neutral"]))


def _emotion_snapshot(state: "EmotionalState") -> Dict[str, float]:
    """把状态压成可存入关系记忆的纯数值快照。"""
    return {ch.value: float(getattr(state, ch.value, 0.0)) for ch in Channel}


def _dominant_channel(state: "EmotionalState") -> str:
    """返回当前最突出的情绪通道标签"""
    candidates = [
        ("joy_dominant",      state.joy),
        ("sadness_dominant",  state.sadness),
        ("anger_dominant",    state.anger),
        ("fear_dominant",     state.fear),
        ("surprise_dominant", state.surprise),
        ("longing_dominant",  state.longing),
        ("guilt_dominant",    state.guilt),
    ]
    best = max(candidates, key=lambda x: x[1])
    return best[0] if best[1] > 0.3 else "neutral"


def _mood_hint(s: "EmotionalState", dominant: str, atmosphere: float,
               tolerance: Optional[Dict[str, object]] = None) -> str:
    """将情绪数值翻译成 LLM 能理解的口吻描述"""
    parts = []

    # 主导情绪 → 核心语气
    dom_hints = {
        "joy_dominant":      "你很开心，语气轻快上扬，可能带笑",
        "sadness_dominant":  "你很难过，语气低沉缓慢，话少",
        "anger_dominant":    "你在生气，语气尖锐或冷淡，可能带刺",
        "fear_dominant":     "你很不安，语气犹豫颤抖，回避正面回答",
        "surprise_dominant": "你很惊讶，语气急促，可能语无伦次",
        "longing_dominant":  "你在思念某人，语气温柔带苦涩，话尾拖长",
        "guilt_dominant":    "你在愧疚，语气自责收敛，不敢直视",
        "neutral":           "你心情平静，语气平稳自然",
    }
    if dominant in dom_hints:
        parts.append(dom_hints[dominant])

    # 信任度
    if s.trust < 0.2:
        parts.append("你很不信任对方，说话防备疏离。任何亲密称呼都让你反感")
    elif s.trust > 0.7:
        parts.append("你非常信任对方，说话坦诚放松")

    # 亲密度：trust+love 双高才接受亲密
    intimacy = (s.trust + s.love) / 2
    if intimacy > 0.7:
        parts.append("你和对方很亲密，可以接受亲昵称呼，甚至会回应")
    elif intimacy < 0.3:
        parts.append("你和对方不熟，亲密称呼让你尴尬，会冷淡拒绝")

    # 爱
    if s.love > 0.6:
        parts.append("你很在乎对方，话里带关切")
    elif s.love < 0.1:
        parts.append("你不在意对方，冷淡敷衍")

    # 氛围：+1 紧绷 / -1 轻松
    if atmosphere > 0.3:
        parts.append("氛围紧绷，你紧张克制")
    elif atmosphere < -0.3:
        parts.append("氛围轻松，你比较放得开")

    # 耐受窗：超过容量后不是“更激烈地演”，而是失去调节能力
    zone = tolerance.get("zone") if tolerance else None
    if zone == ZONE_HYPER:
        parts.append("你已经超出耐受窗：容易战或逃，语气更冲、更防御，很难继续忍耐")
    elif zone == ZONE_HYPO:
        parts.append("你已经超出耐受窗并进入低唤醒：反应变慢、想关机、话变少，这不是平静")

    # 愧疚
    if s.guilt > 0.3:
        parts.append("你做错了事，想弥补或道歉")

    # 思念
    if s.longing > 0.3:
        parts.append("你在想一个人，话里带等待的情绪")

    return "。".join(parts) + "。"


def _build_llm_prompt(engine: Any, user_input: str,
                      character_name: str, character_desc: str,
                      recent_memories: List[Any],
                      memory_context: str = "") -> str:
    """构建 LLM prompt——引擎状态已注入，LLM 只做表达。

    memory_context 来自 memory.MemoryService.build_context()，
    是关系域、压缩后的记忆区块；优先于旧版的 recent_memories。
    """
    s = engine.state
    mem_text = ""
    if memory_context:
        mem_text = memory_context
    elif recent_memories:
        mem_text = "Relevant memories:\n" + "\n".join(
            f"- {m.content}" for m in recent_memories[:3]
        )

    dominant = _dominant_channel(s)
    # 将数值翻译成 AI 能理解的行为指令
    mood_hint = _mood_hint(
        s, dominant, engine.atmosphere, engine.tolerance.to_dict(),
    )

    return f"""You are {character_name}. {character_desc}

Your current emotional state:
  {mood_hint}
  (joy={s.joy:.1f} sad={s.sadness:.1f} anger={s.anger:.1f} fear={s.fear:.1f}
   trust={s.trust:.1f} love={s.love:.1f} guilt={s.guilt:.1f} longing={s.longing:.1f})

{mem_text}

User said: "{user_input}"

Respond in character. 2-3 natural Chinese sentences, spoken aloud (not narration).
Your mood affects HOW you speak — see the mood hint above and let it color your tone."""


def _expression_params(state: "EmotionalState", zone: str = "regulated") -> Dict[str, float]:
    """Live2D 表情映射。regulated 保持原曲线，hyper/hypo 叠加一致性修正。

    hyper：紧张防御——眉压更低、嘴更收紧、眼睛睁大、目光回避、脸红受抑。
    hypo：低唤醒关机——整体幅度压平，眼睛半闭、目光下垂、头微垂。
    """
    brow = -0.5 + state.joy * 0.8 - state.sadness * 0.6
    mouth = -0.3 + state.joy * 0.7 - state.anger * 0.3
    eye = 0.8 + state.surprise * 0.4 - state.fear * 0.1
    blush = min(1.0, state.joy * 0.4 + state.guilt * 0.3)
    gaze = state.longing * 0.5 - state.fear * 0.3
    head = state.sadness * 0.3 + state.guilt * 0.2

    if zone == ZONE_HYPER:
        brow -= 0.15
        mouth -= 0.20
        eye += 0.15
        blush *= 0.55
        gaze -= 0.20
    elif zone == ZONE_HYPO:
        brow = brow * 0.55 - 0.18
        mouth = mouth * 0.45 - 0.10
        eye *= 0.62
        blush *= 0.35
        gaze *= 0.45
        head = head * 0.60 + 0.10

    values = {
        "brow_angle": brow,
        "mouth_curve": mouth,
        "eye_open": eye,
        "blush": blush,
        "gaze_direction": gaze,
        "head_tilt": head,
    }
    if zone in (ZONE_HYPER, ZONE_HYPO):
        values = {
            key: max(-1.0, min(1.0, value))
            for key, value in values.items()
        }
    return {key: round(value, 3) for key, value in values.items()}
