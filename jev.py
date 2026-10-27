"""JEV v0 —— 开放文本 → 六维 Appraisal 的轻量评估器。

设计边界：
- 规则优先，零网络依赖；LLM 只在显式开启且低置信度时补充。
- 只产出 Appraisal / confidence / evidence，不写情绪状态、人格或记忆。
- 缓存、批量、超时、fallback 都在本模块内；调用方永远拿到可用结果。

六维定义见 core.Appraisal：
  goal_relevance     事件与目标的相关程度      0 ~ 1
  goal_conduciveness 事件助力 / 阻碍目标      -1 ~ +1
  expectedness       事件的可预期程度          0 ~ 1
  coping_potential   我能控制 / 应对的程度     0 ~ 1
  other_agency       外部 / 他人归因占比       0 ~ 1
  social_evaluation  对方对我的社会评价       -1 ~ +1
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from core import APPRAISAL_LIMITS, Appraisal
from llm_client import call_llm

NEUTRAL_CONFIDENCE = 0.15

# 线索词表。中文按子串匹配，英文按单词边界匹配。
_CUE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "positive": (
        "喜欢", "爱", "开心", "高兴", "快乐", "幸福", "谢谢", "感谢",
        "太好了", "真好", "棒", "厉害", "可爱", "温柔", "想你", "抱抱",
        "亲亲", "陪你", "相信", "信任", "支持", "理解", "原谅",
        "别担心", "没事", "成功", "赢了", "好消息", "惊喜", "期待",
        "不错", "很好", "挺好", "真不错",
        "good", "love", "like", "happy", "glad", "thanks", "thank you",
        "great", "awesome", "amazing", "cute", "miss you", "hug",
        "trust", "support", "understand", "forgive", "proud",
        "success", "win", "good news", "excited",
    ),
    "negative": (
        "讨厌", "恨", "烦", "生气", "愤怒", "失望", "难过", "伤心",
        "哭", "害怕", "恐惧", "焦虑", "紧张", "压力", "累", "孤独",
        "背叛", "欺骗", "被骗", "撒谎", "离开", "分手", "糟糕",
        "坏消息", "失败", "输", "痛苦", "委屈", "羞耻", "尴尬",
        "难受", "不舒服",
        "不喜欢", "不爱", "不开心", "不高兴", "不想",
        "hate", "angry", "annoyed", "disappointed", "sad", "cry",
        "afraid", "scared", "anxious", "nervous", "stressed", "tired",
        "lonely", "betray", "lie", "lied", "leave", "break up",
        "awful", "bad news", "fail", "lose", "pain", "hurt",
        "ashamed", "embarrassed", "upset",
    ),
    "insult": (
        "傻", "蠢", "白痴", "废物", "垃圾", "滚", "闭嘴", "没用",
        "恶心", "丑陋", "去死",
        "stupid", "idiot", "loser", "useless", "trash", "shut up",
        "disgusting", "ugly", "worthless",
    ),
    "praise": (
        "你真棒", "你真厉害", "你真好", "好可爱", "很温柔", "喜欢你",
        "爱你", "聪明", "善良", "可靠",
        "you're great", "you are great", "so kind", "so smart",
        "so cute", "love you", "proud of you",
    ),
    "unexpected": (
        "竟然", "居然", "没想到", "突然", "意外", "震惊", "不会吧",
        "unexpected", "suddenly", "surprised", "can't believe",
        "cannot believe", "shocking",
    ),
    "routine": (
        "又", "总是", "每次", "照常", "一如既往", "还是这样",
        "again", "always", "every time", "as usual",
    ),
    "self_agency": (
        "我能", "我可以", "我会", "我来", "我决定", "我选择", "我控制",
        "让我来", "我自己",
        "i can", "i will", "i'll", "i decide", "i choose", "let me",
    ),
    "helpless": (
        "没办法", "无能为力", "只能", "不得不", "控制不了", "做不到",
        "帮不了", "无能为力", "身不由己",
        "helpless", "no choice", "can't help", "cannot help",
        "can't do", "unable",
    ),
    "other_agency": (
        "你让我", "你害", "因为你", "都怪你", "他让我", "她让我",
        "他们", "公司", "老板", "老师", "朋友",
        "you made me", "because of you", "he made", "she made",
        "they", "boss", "company",
    ),
    "apology": (
        "对不起", "抱歉", "我错了", "原谅我", "是我的错",
        "sorry", "apologize", "my fault",
    ),
    "rejection": (
        "拒绝", "不要你", "不喜欢你", "走开", "别烦我", "离我远点",
        "reject", "don't like you", "go away", "leave me alone",
    ),
    "intimacy": (
        "老婆", "老公", "宝贝", "亲爱的", "亲我", "抱我", "嫁给我",
        "娶我", "做我女朋友", "做我男朋友",
        "kiss me", "marry me", "be my girlfriend", "be my boyfriend",
        "darling", "honey", "baby",
    ),
    "commitment": (
        "永远", "一直", "一辈子", "承诺", "答应你", "不会离开",
        "陪你", "我保证",
        "forever", "always", "promise", "never leave", "stay with you",
    ),
    "question": (
        "为什么", "怎么", "什么", "真的吗", "不会吧", "？", "?",
        "why", "how", "what", "really",
    ),
}

_NEGATIONS_CN = ("不", "没", "没有", "别", "无", "未", "并非")
_NEGATIONS_EN = ("not ", "don't ", "do not ", "never ", "no ")
_NEGATION_GROUPS = frozenset({"positive", "negative", "insult", "praise"})

_ASCII_RE = re.compile(r"^[a-z0-9' ]+$")


@dataclass(frozen=True)
class JEVResult:
    """一次文本评估的完整结果；对调用方只读。"""

    appraisal: Appraisal
    confidence: float
    evidence: Tuple[str, ...] = ()
    source: str = "rules"          # rules / llm / fallback
    cached: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "appraisal": self.appraisal.to_dict(),
            "confidence": round(self.confidence, 4),
            "evidence": list(self.evidence),
            "source": self.source,
            "cached": self.cached,
        }


def _clamp(name: str, value: Any, default: float) -> float:
    lo, hi = APPRAISAL_LIMITS[name]
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:  # NaN
        number = float(default)
    return max(lo, min(hi, number))


def make_appraisal(values: Optional[Mapping[str, Any]] = None) -> Appraisal:
    """把任意映射夹到合法六维；缺省或非法维度用中性值。"""
    values = values or {}
    defaults = Appraisal().to_dict()
    return Appraisal(**{
        name: _clamp(name, values.get(name, defaults[name]), defaults[name])
        for name in APPRAISAL_LIMITS
    })


def _is_negated(text: str, index: int) -> bool:
    window = text[max(0, index - 6):index]
    if any(window.endswith(neg) for neg in _NEGATIONS_CN):
        return True
    return any(neg in window for neg in _NEGATIONS_EN)


def _find_cue(text: str, cue: str) -> Optional[int]:
    """返回首个非否定出现位置；英文用词边界避免子串误命中。"""
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


def _match_cues(text: str) -> List[Tuple[str, str]]:
    hits: List[Tuple[str, str]] = []
    for group, cues in _CUE_GROUPS.items():
        for cue in cues:
            if _find_cue(text, cue) is not None:
                hits.append((group, cue))
    return hits


def _rule_result(text: str) -> JEVResult:
    hits = _match_cues(text)
    counts = {group: 0 for group in _CUE_GROUPS}
    for group, _ in hits:
        counts[group] += 1
    has = {group: count > 0 for group, count in counts.items()}
    matched_groups = sum(1 for value in has.values() if value)

    relevance = 0.15
    if has["positive"] or has["negative"]:
        relevance += 0.45
    if has["insult"]:
        relevance += 0.55
    elif has["praise"]:
        relevance += 0.20
    if has["rejection"]:
        relevance += 0.35
    elif has["intimacy"] or has["commitment"]:
        relevance += 0.25
    if has["question"]:
        relevance += 0.05
    relevance = min(0.95, relevance)

    pos = min(3, counts["positive"] + counts["praise"] + counts["apology"] + counts["commitment"])
    neg = min(3, counts["negative"] + counts["insult"] + counts["rejection"])
    if pos + neg:
        conduciveness = (pos - neg) / max(1, pos + neg) * 0.9
    else:
        conduciveness = 0.0

    expectedness = 0.5
    expectedness -= 0.25 * min(1, counts["unexpected"])
    expectedness += 0.20 * min(1, counts["routine"])
    expectedness -= 0.10 * min(1, counts["question"])

    coping = 0.5
    coping += 0.20 * min(1, counts["self_agency"])
    coping -= 0.25 * min(1, counts["helpless"])
    if has["other_agency"] and not has["self_agency"]:
        coping -= 0.05

    other_agency = 0.30
    other_agency += 0.40 * min(1, counts["other_agency"])
    other_agency -= 0.20 * min(1, counts["self_agency"])

    social = 0.0
    social += 0.60 * min(1, counts["praise"])
    social -= 0.70 * min(1, counts["insult"])
    social -= 0.50 * min(1, counts["rejection"])
    social += 0.15 * min(1, counts["apology"])
    social += 0.15 * min(1, counts["positive"])
    social -= 0.15 * min(1, counts["negative"])

    appraisal = make_appraisal({
        "goal_relevance": relevance,
        "goal_conduciveness": conduciveness,
        "expectedness": expectedness,
        "coping_potential": coping,
        "other_agency": other_agency,
        "social_evaluation": social,
    })

    if not hits:
        return JEVResult(appraisal, NEUTRAL_CONFIDENCE, (), "fallback")

    confidence = 0.20 + 0.15 * matched_groups + 0.03 * min(5, len(hits))
    evidence = tuple(f"{group}:{cue}" for group, cue in hits[:6])
    return JEVResult(appraisal, min(0.95, confidence), evidence, "rules")


def _load_json_object(raw: str) -> Optional[Dict[str, Any]]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


_LLM_PROMPT = """你是情绪认知评估器。根据用户输入评估六维 Appraisal，只输出 JSON。

维度（都必须给数值）：
- goal_relevance: 0~1，事件与角色目标的相关程度
- goal_conduciveness: -1~1，事件助力(+)或阻碍(-)目标
- expectedness: 0~1，事件的可预期程度
- coping_potential: 0~1，角色能控制/应对的程度
- other_agency: 0~1，外部/他人归因占比
- social_evaluation: -1~1，对方对角色(角色会感知到)的社会评价

输出格式：
{"appraisal": {"goal_relevance": 0.0, "goal_conduciveness": 0.0,
"expectedness": 0.5, "coping_potential": 0.5, "other_agency": 0.0,
"social_evaluation": 0.0}, "confidence": 0.0, "evidence": ["简短依据"]}

用户输入：{text}
"""


class JEV:
    """规则优先的文本评估器；可选 LLM 只做低置信度补充。"""

    def __init__(self, *, use_llm: bool = False,
                 confidence_threshold: float = 0.45,
                 cache_size: int = 256,
                 timeout: float = 8.0,
                 max_batch: int = 64,
                 llm_options: Optional[Mapping[str, Any]] = None) -> None:
        self.use_llm = bool(use_llm)
        self.confidence_threshold = max(0.0, min(1.0, float(confidence_threshold)))
        self.cache_size = max(0, int(cache_size))
        self.timeout = float(timeout)
        self.max_batch = max(1, int(max_batch))
        self.llm_options: Dict[str, Any] = dict(llm_options or {})
        self._cache: "OrderedDict[Tuple[str, bool, bool], JEVResult]" = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    # ── public API ─────────────────────────────────────

    def analyze(self, text: str, *, use_llm: Optional[bool] = None,
                force_llm: bool = False) -> JEVResult:
        """评估一条文本。任何异常都会回退到规则或中性结果。"""
        normalized = str(text or "").strip()
        if not normalized:
            return JEVResult(make_appraisal(), 0.0, (), "fallback")

        llm_enabled = self.use_llm if use_llm is None else bool(use_llm)
        key = (normalized, llm_enabled, bool(force_llm))
        cached = self._cache_get(key)
        if cached is not None:
            return replace(cached, cached=True)

        result = _rule_result(normalized)
        if llm_enabled and (force_llm or result.confidence < self.confidence_threshold):
            llm_result = self._analyze_with_llm(normalized, result.appraisal)
            if llm_result is not None:
                result = llm_result

        self._cache_put(key, result)
        return result

    def analyze_many(self, texts: Iterable[str], *,
                     use_llm: Optional[bool] = None,
                     force_llm: bool = False) -> List[JEVResult]:
        """批量评估；按 max_batch 分块，避免一次调用无界增长。"""
        items = list(texts)
        results: List[JEVResult] = []
        for start in range(0, len(items), self.max_batch):
            chunk = items[start:start + self.max_batch]
            results.extend(
                self.analyze(text, use_llm=use_llm, force_llm=force_llm)
                for text in chunk
            )
        return results

    def reset_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def cache_stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "size": len(self._cache),
                "capacity": self.cache_size,
                "hits": self._hits,
                "misses": self._misses,
            }

    # ── LLM 补充 ───────────────────────────────────────

    def _analyze_with_llm(self, text: str,
                          base: Appraisal) -> Optional[JEVResult]:
        options: Dict[str, Any] = {
            "timeout": self.timeout,
            "max_tokens": 320,
            "temperature": 0.0,
        }
        options.update(self.llm_options)
        try:
            raw, _backend = call_llm(_LLM_PROMPT.replace("{text}", text), **options)
        except Exception:
            return None
        if not raw:
            return None

        payload = _load_json_object(raw)
        if payload is None:
            return None
        values = payload.get("appraisal")
        if not isinstance(values, dict):
            values = payload
        if not isinstance(values, dict):
            return None

        merged = base.to_dict()
        for name in APPRAISAL_LIMITS:
            if name in values:
                merged[name] = values[name]
        appraisal = make_appraisal(merged)

        try:
            confidence = float(payload.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        raw_evidence = payload.get("evidence", ())
        if isinstance(raw_evidence, str):
            raw_evidence = [raw_evidence]
        if not isinstance(raw_evidence, (list, tuple)):
            raw_evidence = []
        evidence = tuple(str(item)[:120] for item in list(raw_evidence)[:6])
        return JEVResult(appraisal, confidence, evidence, "llm")

    # ── cache ──────────────────────────────────────────

    def _cache_get(self, key: Tuple[str, bool, bool]) -> Optional[JEVResult]:
        if self.cache_size <= 0:
            return None
        with self._lock:
            value = self._cache.get(key)
            if value is None:
                self._misses += 1
                return None
            self._hits += 1
            self._cache.move_to_end(key)
            return value

    def _cache_put(self, key: Tuple[str, bool, bool], value: JEVResult) -> None:
        if self.cache_size <= 0:
            return
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)


_DEFAULT_JEV = JEV()


def analyze(text: str, *, use_llm: Optional[bool] = None,
            force_llm: bool = False) -> JEVResult:
    """模块级便利函数，共享默认缓存。"""
    return _DEFAULT_JEV.analyze(text, use_llm=use_llm, force_llm=force_llm)


def analyze_many(texts: Iterable[str], *, use_llm: Optional[bool] = None,
                 force_llm: bool = False) -> List[JEVResult]:
    return _DEFAULT_JEV.analyze_many(texts, use_llm=use_llm, force_llm=force_llm)


def reset_cache() -> None:
    _DEFAULT_JEV.reset_cache()


def cache_stats() -> Dict[str, int]:
    return _DEFAULT_JEV.cache_stats()
