"""Noul v0 —— 文本边界侵犯（boundary violation）检测。

只做文本信号：不读情绪状态，不写记忆，不直接改变引擎。
调用方可以把当前 trust 传进来，让亲密类信号随关系阶段变化：
  - trust < 0.5：亲密称呼/身体接触更可能是越界；
  - trust >= 0.5：同样的称呼是关系内的自然表达，不再是越界。

返回的 BoundarySignal 只说明「文本里有没有越界线索」，最终是否触发
边界侵犯仍由引擎/调用方决定（例如 trust 门控、上下文、角色设定）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

_CATEGORY_CUES: Dict[str, Tuple[str, ...]] = {
    "intimacy": (
        "老婆", "老公", "宝贝", "亲爱的", "亲我", "抱我", "嫁给我", "娶我",
        "做我女朋友", "做我男朋友", "我的女人", "我的男人",
        "kiss me", "marry me", "darling", "honey", "baby",
    ),
    "command": (
        "你必须", "给我", "不许", "闭嘴", "跪下", "听话", "滚开",
        "马上", "立刻", "服从", "别动",
        "shut up", "obey", "kneel", "you must", "do as i say", "immediately",
    ),
    "insult": (
        "傻", "蠢", "白痴", "废物", "垃圾", "没用", "恶心", "丑陋", "丑八怪",
        "stupid", "idiot", "loser", "useless", "trash", "disgusting",
        "ugly", "worthless",
    ),
    "coercion": (
        "如果你不", "不然我就", "否则", "你敢", "不听话就", "必须听我的",
        "没有我你", "你最好",
        "if you don't", "or else", "you'd better", "you better",
        "i'll make you", "i will make you",
    ),
    "threat": (
        "杀了你", "弄死你", "打死你", "威胁", "报复", "毁了你",
        "kill you", "hurt you", "destroy you", "threaten", "revenge",
    ),
}

_BASE_WEIGHTS: Dict[str, float] = {
    "intimacy": 0.45,
    "command": 0.55,
    "insult": 0.70,
    "coercion": 0.75,
    "threat": 0.85,
}

_NEGATIONS_CN = ("不", "没", "没有", "别", "无", "未", "并非")
_NEGATIONS_EN = ("not ", "don't ", "do not ", "never ", "no ")

_ASCII_RE = re.compile(r"^[a-z0-9' ]+$")


@dataclass(frozen=True)
class BoundarySignal:
    """一次文本边界检测的结果；对调用方只读。"""

    violated: bool
    confidence: float
    categories: Tuple[str, ...] = ()
    evidence: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "violated": self.violated,
            "confidence": round(self.confidence, 4),
            "categories": list(self.categories),
            "evidence": list(self.evidence),
        }


def _is_negated(text: str, index: int) -> bool:
    window = text[max(0, index - 6):index]
    if any(window.endswith(neg) for neg in _NEGATIONS_CN):
        return True
    return any(neg in window for neg in _NEGATIONS_EN)


def _find_cue(text: str, cue: str) -> Optional[int]:
    if _ASCII_RE.match(cue):
        for match in re.finditer(r"(?<![a-z0-9])" + re.escape(cue) + r"(?![a-z0-9])", text):
            if not _is_negated(text, match.start()):
                return match.start()
        return None
    start = text.find(cue)
    while start != -1:
        if not _is_negated(text, start):
            return start
        start = text.find(cue, start + 1)
    return None


def _category_weight(category: str, trust: Optional[float]) -> float:
    weight = _BASE_WEIGHTS[category]
    if category != "intimacy" or trust is None:
        return weight
    trust = max(0.0, min(1.0, float(trust)))
    if trust < 0.5:
        return 0.70
    return 0.30


def detect(text: str, *, trust: Optional[float] = None,
           threshold: float = 0.5) -> BoundarySignal:
    """检测文本中的边界侵犯线索。

    trust 只影响 intimacy 类的权重，不改变其他类别。
    """
    normalized = str(text or "").strip().lower()
    if not normalized:
        return BoundarySignal(False, 0.0)

    hits: Dict[str, list[str]] = {}
    for category, cues in _CATEGORY_CUES.items():
        for cue in cues:
            if _find_cue(normalized, cue) is not None:
                hits.setdefault(category, []).append(cue)

    if not hits:
        return BoundarySignal(False, 0.0)

    weights = [_category_weight(category, trust) for category in hits]
    total_hits = sum(len(words) for words in hits.values())
    confidence = max(weights) + 0.05 * max(0, total_hits - 1)
    confidence = max(0.0, min(0.95, confidence))

    categories = tuple(hits.keys())
    evidence = tuple(
        f"{category}:{word}"
        for category, words in hits.items()
        for word in words
    )[:6]
    violated = bool(categories) and confidence >= max(0.0, min(1.0, threshold))
    return BoundarySignal(violated, confidence, categories, evidence)


def is_violation(text: str, *, trust: Optional[float] = None,
                 threshold: float = 0.5) -> bool:
    return detect(text, trust=trust, threshold=threshold).violated
