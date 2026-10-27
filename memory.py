"""
Relationship Memory System v0.1
===============================

面向 AI 伴侣 / NPC 的「关系域」长期记忆。

核心边界：
  - 记忆按 (character_id, user_id) 隔离，绝不跨用户串线。
  - 记忆影响 appraisal / relationship / prompt，但不写 Personality。
  - 长期存储是「卡片 + 结构化事实 + 关系摘要」，不是全量聊天记录。
  - 原始聊天只作为冷存档引用；进入 prompt 的永远是压缩后的记忆。

设计参考（只借鉴结构，不直接依赖）：
  - Mem0: user/agent/run 作用域、事实抽取、混合检索
  - AgentMemory: L0 原文 → L1 事实 → L2 场景 → L3 关系叙事
  - claude-memory-skill: 分层摘要 + 按需读取 + 用户可编辑

用法：
    from memory import RelationshipMemoryStore, MemoryService

    store = RelationshipMemoryStore("memory.db")
    service = MemoryService(store)
    service.remember_turn("furina", "alice", "我叫小枫，我不喜欢被说教。")
    context = service.build_context("furina", "alice", "今天又被老板骂了。")
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

DEFAULT_DB_PATH = "memory.db"
# SQLite PRAGMA user_version：0 = L4 之前的旧库；1 = 当前基线 schema。
# 每次改表结构时递增，并在 RelationshipMemoryStore._migrate_schema()
# 里补一段 from_version < N 的迁移。
MEMORY_SCHEMA_VERSION = 1

CHANNELS = (
    "joy", "sadness", "anger", "fear", "love",
    "disgust", "surprise", "trust", "longing", "guilt",
)

# 记忆类型对「关系重要性」的先验权重。
KIND_IMPORTANCE = {
    "boundary": 1.00,
    "promise": 0.95,
    "milestone": 0.90,
    "conflict": 0.85,
    "repair": 0.85,
    "preference": 0.75,
    "fact": 0.70,
    "episode": 0.55,
    "note": 0.40,
}

DEFAULT_RELATIONSHIP_SUMMARY = ""

# L4: 六维 Appraisal 是记忆先验唯一允许进入的接口。
APPRAISAL_DIMS = (
    "goal_relevance",
    "goal_conduciveness",
    "expectedness",
    "coping_potential",
    "other_agency",
    "social_evaluation",
)

# 中性事件的评估基线。记忆卡片存的是事件当时的绝对值，
# 先减去中性基线，才得到“这段记忆把下一次评估往哪边推”。
APPRAISAL_NEUTRAL = {
    "goal_relevance": 0.0,
    "goal_conduciveness": 0.0,
    "expectedness": 0.5,
    "coping_potential": 0.5,
    "other_agency": 0.0,
    "social_evaluation": 0.0,
}

_STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "和",
    "就", "不", "也", "都", "这", "那", "吗", "呢", "啊", "吧", "呀",
    "一个", "一下", "什么", "怎么", "为什么", "因为", "所以", "但是",
}

_CJK_RE = re.compile(r"[一-鿿]+")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


# ══════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════

@dataclass
class MemoryCard:
    """一张可进入 prompt 的记忆卡片。summary 控制在 30–80 字。"""

    summary: str
    character_id: str = "default"
    user_id: str = "default"
    scope: str = "relation"          # self / relation / world / session
    kind: str = "episode"
    tags: List[str] = field(default_factory=list)
    appraisal: Dict[str, float] = field(default_factory=dict)
    emotion_snap: Dict[str, float] = field(default_factory=dict)
    salience: float = 0.5
    confidence: float = 0.8
    source: str = "observed"         # user_said / observed / inferred / canon
    raw_ref: Optional[str] = None    # 指向 messages / turn id，不直接进 prompt
    status: str = "active"           # active / superseded / deleted
    created_at: float = field(default_factory=time.time)
    valid_from: Optional[float] = None
    valid_to: Optional[float] = None
    id: Optional[int] = None
    last_access: Optional[float] = None
    access_count: int = 0


@dataclass
class UserFact:
    """结构化事实：比一段自然语言更好去重、更好更新。"""

    key: str
    value: str
    character_id: str = "default"
    user_id: str = "default"
    category: str = "general"
    confidence: float = 0.8
    source: str = "user_said"
    evidence: List[str] = field(default_factory=list)
    status: str = "active"           # active / superseded / deleted
    first_seen: float = field(default_factory=time.time)
    last_confirmed: float = field(default_factory=time.time)
    id: Optional[int] = None


@dataclass
class RelationshipState:
    """角色与某个用户之间的关系状态。不是人格。"""

    character_id: str = "default"
    user_id: str = "default"
    trust: float = 0.2
    love: float = 0.2
    familiarity: float = 0.0
    boundaries: List[str] = field(default_factory=list)
    summary: str = DEFAULT_RELATIONSHIP_SUMMARY
    updated_at: float = field(default_factory=time.time)


@dataclass
class OpenLoop:
    """承诺、待办、未解决的冲突。最容易让 AI 显得「真的记得」。"""

    summary: str
    character_id: str = "default"
    user_id: str = "default"
    due_at: Optional[float] = None
    status: str = "open"             # open / closed / cancelled
    importance: float = 0.5
    created_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None
    id: Optional[int] = None


@dataclass
class RecallResult:
    cards: List[MemoryCard] = field(default_factory=list)
    facts: List[UserFact] = field(default_factory=list)
    relationship: Optional[RelationshipState] = None
    open_loops: List[OpenLoop] = field(default_factory=list)
    query: str = ""


@dataclass
class MemoryCandidate:
    """抽取器产出的候选记忆；尚未入库。"""

    kind: str
    summary: str
    fact_key: Optional[str] = None
    fact_value: Optional[str] = None
    category: str = "general"
    confidence: float = 0.7
    tags: List[str] = field(default_factory=list)


@dataclass
class MemoryPrior:
    """一次召回得到的只读先验。

    appraisal 进入 engine.tick(appraisal_bias=...)，channels 保留给
    relationship / prompt 侧使用；两者都不能直接写情绪状态或人格。
    """

    appraisal: Dict[str, float] = field(default_factory=dict)
    channels: Dict[str, float] = field(default_factory=dict)
    card_count: int = 0
    total_weight: float = 0.0


@dataclass
class GateDecision:
    accept: bool
    reason: str
    action: str = "write"


# ══════════════════════════════════════════════════════
# 文本处理
# ══════════════════════════════════════════════════════

def tokenize(text: str) -> List[str]:
    """中英混合的轻量分词：英文词 + 中文二元组。

    不依赖 jieba，也不依赖 embedding。中文用 overlap bigram，
    对「老板 / 项目 / 周五」这类短查询足够稳定。
    """
    text = (text or "").lower()
    tokens: List[str] = []
    seen = set()

    def add(token: str):
        token = token.strip()
        if token and token not in _STOPWORDS and token not in seen:
            seen.add(token)
            tokens.append(token)

    for match in _WORD_RE.finditer(text):
        add(match.group(0))

    for match in _CJK_RE.finditer(text):
        run = match.group(0)
        if len(run) == 1:
            add(run)
        else:
            for i in range(len(run) - 1):
                add(run[i:i + 2])

    return tokens


def _serialize(values: Any) -> str:
    return json.dumps(values, ensure_ascii=False)


def _deserialize(value: str, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_key(key: str) -> str:
    key = (key or "").strip().lower()
    key = re.sub(r"\s+", ".", key)
    return key[:80] or "fact"


def _as_dict(value: Any) -> Dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): float(v) for k, v in value.items() if isinstance(v, (int, float))}
    if hasattr(value, "to_dict"):
        try:
            return {str(k): float(v) for k, v in value.to_dict().items()
                    if isinstance(v, (int, float))}
        except (TypeError, ValueError):
            return {}
    return {}


def _salience_from_emotion(emotion_snap: Dict[str, float]) -> float:
    if not emotion_snap:
        return 0.5
    strength = max(emotion_snap.values(), default=0.0)
    return max(0.2, min(1.0, strength))


def _relative_time(timestamp: float, now: Optional[float] = None) -> str:
    now = now or time.time()
    age = max(0.0, now - timestamp)
    if age < 86400:
        return "今天，"
    if age < 2 * 86400:
        return "昨天，"
    if age < 7 * 86400:
        return f"{int(age // 86400)}天前，"
    if age < 30 * 86400:
        return f"{int(age // (7 * 86400))}周前，"
    return time.strftime("%Y-%m", time.localtime(timestamp)) + "，"


def _format_due(due_at: Optional[float], now: Optional[float] = None) -> str:
    if not due_at:
        return ""
    now = now or time.time()
    delta_days = (due_at - now) / 86400.0
    if delta_days < 0:
        return "（已到期）"
    if delta_days < 1:
        return "（今天）"
    if delta_days < 3:
        return f"（{int(delta_days)}天内）"
    return ""


def _lexical_score(query_tokens: Iterable[str], doc_tokens: Iterable[str]) -> float:
    query_set = set(query_tokens)
    if not query_set:
        return 0.0
    doc_set = set(doc_tokens)
    if not doc_set:
        return 0.0
    return len(query_set & doc_set) / float(len(query_set))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _truncate_to_budget(lines: Sequence[str], budget_chars: int,
                        reserved_suffix: str = "") -> str:
    """按字符预算拼 prompt，保留末尾行为提醒。"""
    budget = max(120, budget_chars - len(reserved_suffix) - 2)
    output: List[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if used + cost > budget:
            remaining = budget - used
            if remaining > 12:
                output.append(line[:max(0, remaining - 1)] + "…")
            break
        output.append(line)
        used += cost
    if reserved_suffix:
        output.append(reserved_suffix)
    return "\n".join(output)


# ══════════════════════════════════════════════════════
# SQLite 存储
# ══════════════════════════════════════════════════════

class RelationshipMemoryStore:
    """关系域记忆的 SQLite 实现。

    所有读写都必须带 character_id + user_id；默认 scope='relation'。
    这是一条硬边界：A 用户的记忆永远不会被 B 用户的召回命中。
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db = sqlite3.connect(db_path, timeout=8.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._fts = False
        self.schema_version = MEMORY_SCHEMA_VERSION
        try:
            self._init_schema()
        except Exception:
            self.db.close()
            raise

    # ── schema ──────────────────────────────────────────

    def _init_schema(self) -> None:
        current_version = int(
            self.db.execute("PRAGMA user_version").fetchone()[0]
        )
        if current_version > MEMORY_SCHEMA_VERSION:
            raise RuntimeError(
                "memory schema is newer than this code: "
                f"db={current_version}, code={MEMORY_SCHEMA_VERSION}"
            )

        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS memory_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'relation',
                kind TEXT NOT NULL DEFAULT 'episode',
                summary TEXT NOT NULL,
                search_text TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                appraisal TEXT NOT NULL DEFAULT '{}',
                emotion_snap TEXT NOT NULL DEFAULT '{}',
                salience REAL NOT NULL DEFAULT 0.5,
                confidence REAL NOT NULL DEFAULT 0.8,
                source TEXT NOT NULL DEFAULT 'observed',
                raw_ref TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                valid_from REAL,
                valid_to REAL,
                last_access REAL,
                access_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_cards_scope
                ON memory_cards(character_id, user_id, status, scope);
            CREATE INDEX IF NOT EXISTS idx_cards_time
                ON memory_cards(character_id, user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_cards_salience
                ON memory_cards(character_id, user_id, salience);

            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                confidence REAL NOT NULL DEFAULT 0.8,
                source TEXT NOT NULL DEFAULT 'user_said',
                evidence TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                first_seen REAL NOT NULL,
                last_confirmed REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_facts_lookup
                ON user_facts(character_id, user_id, key, status);

            CREATE TABLE IF NOT EXISTS relationships (
                character_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                trust REAL NOT NULL DEFAULT 0.2,
                love REAL NOT NULL DEFAULT 0.2,
                familiarity REAL NOT NULL DEFAULT 0.0,
                boundaries TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY(character_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS open_loops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                due_at REAL,
                status TEXT NOT NULL DEFAULT 'open',
                importance REAL NOT NULL DEFAULT 0.5,
                created_at REAL NOT NULL,
                closed_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_loops_scope
                ON open_loops(character_id, user_id, status);
        """)
        self.db.commit()
        self._migrate_schema(current_version)
        self.db.execute(f"PRAGMA user_version = {MEMORY_SCHEMA_VERSION}")
        self.db.commit()
        self._init_fts()

    def _migrate_schema(self, from_version: int) -> None:
        """按 PRAGMA user_version 执行迁移。

        0 -> 1：L4 之前的库没有版本号；上面的 CREATE TABLE IF NOT EXISTS
        已经补齐基线表，无需 ALTER。未来版本在这里追加 from_version < N。
        """
        if from_version < 1:
            pass

    def _init_fts(self) -> None:
        """FTS5 只做 lexical 索引；中文先转成 bigram 再写入。"""
        try:
            self.db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(
                    search_text,
                    content='memory_cards',
                    content_rowid='id',
                    tokenize='unicode61'
                )
            """)
            self.db.executescript("""
                CREATE TRIGGER IF NOT EXISTS memory_cards_ai
                AFTER INSERT ON memory_cards BEGIN
                    INSERT INTO memory_fts(rowid, search_text)
                    VALUES (new.id, new.search_text);
                END;

                CREATE TRIGGER IF NOT EXISTS memory_cards_ad
                AFTER DELETE ON memory_cards BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, search_text)
                    VALUES ('delete', old.id, old.search_text);
                END;

                CREATE TRIGGER IF NOT EXISTS memory_cards_au
                AFTER UPDATE ON memory_cards BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, search_text)
                    VALUES ('delete', old.id, old.search_text);
                    INSERT INTO memory_fts(rowid, search_text)
                    VALUES (new.id, new.search_text);
                END;
            """)
            # 只有索引与主表不一致时才 rebuild，避免每次启动都全量重建。
            card_count = self.db.execute(
                "SELECT COUNT(*) FROM memory_cards"
            ).fetchone()[0]
            fts_count = self.db.execute(
                "SELECT COUNT(*) FROM memory_fts"
            ).fetchone()[0]
            if card_count != fts_count:
                self.db.execute(
                    "INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')"
                )
            self.db.commit()
            self._fts = True
        except sqlite3.OperationalError:
            # 某些老 SQLite 没有 FTS5；召回会退回 Python 词法打分。
            self._fts = False

    # ── cards ───────────────────────────────────────────

    def _card_search_text(self, card: MemoryCard) -> str:
        pieces = [card.summary, card.kind, " ".join(card.tags)]
        return " ".join(tokenize(" ".join(pieces)))

    def add_card(self, card: MemoryCard) -> int:
        cursor = self.db.execute(
            """INSERT INTO memory_cards
               (character_id, user_id, scope, kind, summary, search_text,
                tags, appraisal, emotion_snap, salience, confidence, source,
                raw_ref, status, created_at, valid_from, valid_to,
                last_access, access_count)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                card.character_id, card.user_id, card.scope, card.kind,
                card.summary, self._card_search_text(card),
                _serialize(card.tags), _serialize(card.appraisal),
                _serialize(card.emotion_snap), float(card.salience),
                float(card.confidence), card.source, card.raw_ref,
                card.status, float(card.created_at), card.valid_from,
                card.valid_to, card.last_access, int(card.access_count),
            ),
        )
        self.db.commit()
        card.id = int(cursor.lastrowid)
        return card.id

    def get_card(self, card_id: int) -> Optional[MemoryCard]:
        row = self.db.execute(
            "SELECT * FROM memory_cards WHERE id=?", (card_id,)
        ).fetchone()
        return self._row_to_card(row) if row else None

    def _row_to_card(self, row: sqlite3.Row) -> MemoryCard:
        return MemoryCard(
            id=row["id"],
            character_id=row["character_id"],
            user_id=row["user_id"],
            scope=row["scope"],
            kind=row["kind"],
            summary=row["summary"],
            tags=_deserialize(row["tags"], []),
            appraisal=_deserialize(row["appraisal"], {}),
            emotion_snap=_deserialize(row["emotion_snap"], {}),
            salience=row["salience"],
            confidence=row["confidence"],
            source=row["source"],
            raw_ref=row["raw_ref"],
            status=row["status"],
            created_at=row["created_at"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            last_access=row["last_access"],
            access_count=row["access_count"],
        )

    # ── facts ───────────────────────────────────────────

    def upsert_fact(self, character_id: str, user_id: str, key: str,
                    value: str, category: str = "general",
                    confidence: float = 0.8, source: str = "user_said",
                    evidence: Optional[Sequence[str]] = None,
                    now: Optional[float] = None) -> int:
        """同一 key 只有一个 active 值；旧值标记 superseded，不物理删除。"""
        now = now or time.time()
        key = _normalize_key(key)
        value = (value or "").strip()
        if not value:
            raise ValueError("fact value cannot be empty")

        rows = self.db.execute(
            """SELECT * FROM user_facts
               WHERE character_id=? AND user_id=? AND key=? AND status='active'
               ORDER BY last_confirmed DESC""",
            (character_id, user_id, key),
        ).fetchall()

        evidence = list(evidence or [])
        if rows:
            current = rows[0]
            if current["value"].strip() == value:
                old_evidence = _deserialize(current["evidence"], [])
                merged = list(dict.fromkeys(old_evidence + evidence))[-10:]
                self.db.execute(
                    """UPDATE user_facts
                       SET confidence=?, last_confirmed=?, evidence=?
                       WHERE id=?""",
                    (max(current["confidence"], confidence), now,
                     _serialize(merged), current["id"]),
                )
                self.db.commit()
                return int(current["id"])

            self.db.execute(
                "UPDATE user_facts SET status='superseded' WHERE id=?",
                (current["id"],),
            )

        cursor = self.db.execute(
            """INSERT INTO user_facts
               (character_id, user_id, key, value, category, confidence,
                source, evidence, status, first_seen, last_confirmed)
               VALUES (?,?,?,?,?,?,?,?, 'active', ?,?)""",
            (character_id, user_id, key, value, category, confidence,
             source, _serialize(evidence[-10:]), now, now),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def get_facts(self, character_id: str, user_id: str,
                  active_only: bool = True) -> List[UserFact]:
        sql = "SELECT * FROM user_facts WHERE character_id=? AND user_id=?"
        params: List[Any] = [character_id, user_id]
        if active_only:
            sql += " AND status='active'"
        sql += " ORDER BY last_confirmed DESC"
        rows = self.db.execute(sql, params).fetchall()
        return [self._row_to_fact(row) for row in rows]

    def _row_to_fact(self, row: sqlite3.Row) -> UserFact:
        return UserFact(
            id=row["id"],
            character_id=row["character_id"],
            user_id=row["user_id"],
            key=row["key"],
            value=row["value"],
            category=row["category"],
            confidence=row["confidence"],
            source=row["source"],
            evidence=_deserialize(row["evidence"], []),
            status=row["status"],
            first_seen=row["first_seen"],
            last_confirmed=row["last_confirmed"],
        )

    # ── weighted recall helpers ─────────────────────────

    def _weighted_cards(self, character_id: str, user_id: str,
                        tau_days: float = 30.0,
                        now: Optional[float] = None):
        """返回 (card, weight) 列表与总权重；只读，不更新 access_count。"""
        now = now or time.time()
        rows = self.db.execute(
            """SELECT * FROM memory_cards
               WHERE character_id=? AND user_id=? AND status='active'
                 AND scope IN ('relation', 'self')""",
            (character_id, user_id),
        ).fetchall()
        weighted = []
        total_weight = 0.0
        for row in rows:
            card = self._row_to_card(row)
            age_days = max(0.0, now - card.created_at) / 86400.0
            weight = (float(card.salience) * float(card.confidence)
                      * math.exp(-age_days / max(1.0, tau_days)))
            if weight <= 0:
                continue
            weighted.append((card, weight))
            total_weight += weight
        return weighted, total_weight

    def recent_cards(self, character_id: str, user_id: str,
                     days: float = 7.0, limit: int = 50,
                     now: Optional[float] = None) -> List[MemoryCard]:
        """写入闸门用：取最近一段时间的活跃卡片。"""
        now = now or time.time()
        cutoff = now - max(0.0, days) * 86400.0
        rows = self.db.execute(
            """SELECT * FROM memory_cards
               WHERE character_id=? AND user_id=? AND status='active'
                 AND created_at>=?
               ORDER BY created_at DESC LIMIT ?""",
            (character_id, user_id, cutoff, int(limit)),
        ).fetchall()
        return [self._row_to_card(row) for row in rows]

    def has_card_raw_ref(self, character_id: str, user_id: str,
                         raw_ref: str) -> bool:
        row = self.db.execute(
            """SELECT 1 FROM memory_cards
               WHERE character_id=? AND user_id=? AND raw_ref=? LIMIT 1""",
            (character_id, user_id, raw_ref),
        ).fetchone()
        return row is not None

    def appraisal_prior(self, character_id: str, user_id: str,
                        tau_days: float = 30.0, cap: float = 0.2,
                        now: Optional[float] = None) -> Dict[str, float]:
        """把记忆卡片里的六维 appraisal 压成有界先验。

        L4 的唯一入口：返回六维偏移，交给 core.apply_appraisal_bias()。
        没有任何带 appraisal 的卡片时返回全零，不伪造先验。
        """
        weighted, total_weight = self._weighted_cards(
            character_id, user_id, tau_days=tau_days, now=now,
        )
        totals = {dim: 0.0 for dim in APPRAISAL_DIMS}
        weight_with_appraisal = 0.0
        for card, weight in weighted:
            snapshot = card.appraisal or {}
            if not any(dim in snapshot for dim in APPRAISAL_DIMS):
                continue
            weight_with_appraisal += weight
            for dim in APPRAISAL_DIMS:
                value = float(snapshot.get(dim, APPRAISAL_NEUTRAL[dim]))
                totals[dim] += (value - APPRAISAL_NEUTRAL[dim]) * weight

        if weight_with_appraisal <= 0 or total_weight <= 0:
            return {dim: 0.0 for dim in APPRAISAL_DIMS}

        return {
            dim: _clamp(totals[dim] / weight_with_appraisal, -cap, cap)
            for dim in APPRAISAL_DIMS
        }

    def memory_prior(self, character_id: str, user_id: str,
                     baseline: Optional[Dict[str, float]] = None,
                     tau_days: float = 30.0, cap: float = 0.2,
                     now: Optional[float] = None) -> MemoryPrior:
        """一次返回六维 appraisal 先验与十通道情绪先验。"""
        weighted, total_weight = self._weighted_cards(
            character_id, user_id, tau_days=tau_days, now=now,
        )
        return MemoryPrior(
            appraisal=self.appraisal_prior(
                character_id, user_id, tau_days=tau_days, cap=cap, now=now,
            ),
            channels=self.memory_bias(
                character_id, user_id, baseline=baseline,
                tau_days=tau_days, cap=cap, now=now,
            ),
            card_count=len(weighted),
            total_weight=total_weight,
        )

    # ── relationship ────────────────────────────────────

    def get_relationship(self, character_id: str, user_id: str) -> RelationshipState:
        now = time.time()
        self.db.execute(
            """INSERT OR IGNORE INTO relationships
               (character_id, user_id, trust, love, familiarity, boundaries,
                summary, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (character_id, user_id, 0.2, 0.2, 0.0, "[]",
             DEFAULT_RELATIONSHIP_SUMMARY, now),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT * FROM relationships WHERE character_id=? AND user_id=?",
            (character_id, user_id),
        ).fetchone()
        return RelationshipState(
            character_id=row["character_id"],
            user_id=row["user_id"],
            trust=row["trust"],
            love=row["love"],
            familiarity=row["familiarity"],
            boundaries=_deserialize(row["boundaries"], []),
            summary=row["summary"],
            updated_at=row["updated_at"],
        )

    def update_relationship(self, character_id: str, user_id: str,
                            trust: Optional[float] = None,
                            love: Optional[float] = None,
                            familiarity: Optional[float] = None,
                            boundaries: Optional[Sequence[str]] = None,
                            summary: Optional[str] = None,
                            now: Optional[float] = None) -> RelationshipState:
        now = now or time.time()
        self.get_relationship(character_id, user_id)

        updates: List[str] = ["updated_at=?"]
        params: List[Any] = [now]
        for column, value in (("trust", trust), ("love", love),
                              ("familiarity", familiarity)):
            if value is not None:
                updates.append(f"{column}=?")
                params.append(max(0.0, min(1.0, float(value))))
        if boundaries is not None:
            updates.append("boundaries=?")
            params.append(_serialize(list(dict.fromkeys(boundaries))))
        if summary is not None:
            updates.append("summary=?")
            params.append(summary.strip())

        params.extend([character_id, user_id])
        self.db.execute(
            f"UPDATE relationships SET {', '.join(updates)} "
            "WHERE character_id=? AND user_id=?",
            params,
        )
        self.db.commit()
        return self.get_relationship(character_id, user_id)

    def compose_relationship_summary(self, character_id: str, user_id: str,
                                     max_chars: int = 160) -> str:
        """用事实 / 边界 / 开放循环 / 高显著卡片生成确定性摘要。

        不调用 LLM，也不读原文；同一份数据永远得到同一句话。
        """
        facts = self.get_facts(character_id, user_id)
        loops = self.get_open_loops(character_id, user_id, limit=3)
        cards = self.recent_cards(
            character_id, user_id, days=36500.0, limit=20,
        )

        identity = next(
            (fact.value for fact in facts
             if fact.key == "identity.name" and fact.value),
            "",
        )
        preferences = [
            fact.value for fact in facts
            if fact.key.startswith("preference.") and fact.value
        ][:2]
        boundaries = [
            fact.value for fact in facts
            if fact.category == "boundary" and fact.value
        ][:2]

        parts: List[str] = []
        if identity:
            parts.append(f"名字：{identity}")
        if preferences:
            parts.append("偏好：" + "、".join(preferences))
        if boundaries:
            parts.append("边界：" + "、".join(boundaries))
        if loops:
            parts.append("未闭环：" + "、".join(
                loop.summary for loop in loops[:2] if loop.summary
            ))
        if not parts and cards:
            top = max(cards, key=lambda card: (
                float(card.salience), float(card.confidence),
                float(card.created_at),
            ))
            parts.append("记得：" + top.summary[:60])

        summary = "；".join(part for part in parts if part)
        max_chars = max(20, int(max_chars))
        if len(summary) > max_chars:
            summary = summary[:max_chars - 1] + "…"
        return summary

    def update_relationship_summary(self, character_id: str, user_id: str,
                                    max_chars: int = 160,
                                    force: bool = False,
                                    now: Optional[float] = None) -> str:
        """自动刷新关系摘要；数据没有变化时不写库。"""
        summary = self.compose_relationship_summary(
            character_id, user_id, max_chars=max_chars,
        )
        current = self.get_relationship(character_id, user_id)
        if not force and (not summary or summary == current.summary):
            return current.summary
        self.update_relationship(
            character_id, user_id, summary=summary, now=now,
        )
        return summary

    # ── open loops ──────────────────────────────────────

    def add_open_loop(self, summary: str, character_id: str = "default",
                      user_id: str = "default", due_at: Optional[float] = None,
                      importance: float = 0.5,
                      now: Optional[float] = None) -> int:
        now = now or time.time()
        cursor = self.db.execute(
            """INSERT INTO open_loops
               (character_id, user_id, summary, due_at, status,
                importance, created_at, closed_at)
               VALUES (?,?,?,?, 'open', ?,?,NULL)""",
            (character_id, user_id, summary.strip(), due_at,
             max(0.0, min(1.0, importance)), now),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def close_open_loop(self, loop_id: int,
                        now: Optional[float] = None) -> None:
        now = now or time.time()
        self.db.execute(
            "UPDATE open_loops SET status='closed', closed_at=? WHERE id=?",
            (now, loop_id),
        )
        self.db.commit()

    def get_open_loops(self, character_id: str, user_id: str,
                       include_closed: bool = False,
                       limit: int = 50) -> List[OpenLoop]:
        sql = "SELECT * FROM open_loops WHERE character_id=? AND user_id=?"
        params: List[Any] = [character_id, user_id]
        if not include_closed:
            sql += " AND status='open'"
        sql += """ ORDER BY
                   CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,
                   due_at ASC, importance DESC, created_at DESC
                   LIMIT ?"""
        params.append(limit)
        rows = self.db.execute(sql, params).fetchall()
        return [self._row_to_loop(row) for row in rows]

    def _row_to_loop(self, row: sqlite3.Row) -> OpenLoop:
        return OpenLoop(
            id=row["id"],
            character_id=row["character_id"],
            user_id=row["user_id"],
            summary=row["summary"],
            due_at=row["due_at"],
            status=row["status"],
            importance=row["importance"],
            created_at=row["created_at"],
            closed_at=row["closed_at"],
        )

    # ── recall ──────────────────────────────────────────

    def _candidate_cards(self, character_id: str, user_id: str,
                         tokens: Sequence[str], scope: Optional[str],
                         limit: int = 200) -> List[MemoryCard]:
        filters = ["c.character_id=?", "c.user_id=?", "c.status='active'"]
        params: List[Any] = [character_id, user_id]
        if scope:
            filters.append("c.scope=?")
            params.append(scope)
        where = " AND ".join(filters)

        cards: Dict[int, MemoryCard] = {}

        if self._fts and tokens:
            terms = [t.replace('"', '') for t in tokens if t]
            if terms:
                match_query = " OR ".join(f'"{t}"' for t in terms)
                try:
                    rows = self.db.execute(
                        f"""SELECT c.* FROM memory_cards c
                            WHERE c.id IN (
                                SELECT rowid FROM memory_fts
                                WHERE memory_fts MATCH ?
                            ) AND {where}
                            LIMIT ?""",
                        [match_query] + params + [limit],
                    ).fetchall()
                    for row in rows:
                        card = self._row_to_card(row)
                        cards[card.id] = card
                except sqlite3.OperationalError:
                    pass

        # 近期 + 高显著性补候选，保证没有 lexical 命中时仍能想起重要的事。
        extra_sql = f"""SELECT c.* FROM memory_cards c WHERE {where}
                        ORDER BY c.created_at DESC LIMIT ?"""
        for row in self.db.execute(extra_sql, params + [80]).fetchall():
            card = self._row_to_card(row)
            cards[card.id] = card

        salient_sql = f"""SELECT c.* FROM memory_cards c WHERE {where}
                          ORDER BY c.salience DESC LIMIT ?"""
        for row in self.db.execute(salient_sql, params + [50]).fetchall():
            card = self._row_to_card(row)
            cards[card.id] = card

        return list(cards.values())

    def recall_cards(self, query: str, character_id: str, user_id: str,
                     limit: int = 7, min_confidence: float = 0.5,
                     scope: Optional[str] = None,
                     now: Optional[float] = None) -> List[MemoryCard]:
        now = now or time.time()
        query_tokens = tokenize(query)
        candidates = self._candidate_cards(
            character_id, user_id, query_tokens, scope
        )

        scored = []
        for card in candidates:
            if card.confidence < min_confidence:
                continue
            age_days = max(0.0, now - card.created_at) / 86400.0
            recency = math.exp(-age_days / 30.0)
            lexical = _lexical_score(
                query_tokens, tokenize(card.summary + " " + " ".join(card.tags))
            )
            importance = KIND_IMPORTANCE.get(card.kind, 0.5)
            score = (
                0.35 * lexical
                + 0.20 * recency
                + 0.20 * float(card.salience)
                + 0.15 * importance
                + 0.10 * float(card.confidence)
            )
            scored.append((score, card))

        scored.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        selected = [card for _, card in scored[:limit]]

        if selected:
            self.db.executemany(
                """UPDATE memory_cards
                   SET last_access=?, access_count=access_count+1
                   WHERE id=?""",
                [(now, card.id) for card in selected],
            )
            self.db.commit()

        return selected

    def recall_facts(self, query: str, character_id: str, user_id: str,
                     limit: int = 8, min_confidence: float = 0.5,
                     now: Optional[float] = None) -> List[UserFact]:
        now = now or time.time()
        query_tokens = tokenize(query)
        facts = self.get_facts(character_id, user_id, active_only=True)
        scored = []
        for fact in facts:
            if fact.confidence < min_confidence:
                continue
            lexical = _lexical_score(
                query_tokens,
                tokenize(f"{fact.key} {fact.value} {fact.category}"),
            )
            age_days = max(0.0, now - fact.last_confirmed) / 86400.0
            recency = math.exp(-age_days / 90.0)
            category_weight = 1.0 if fact.category in ("boundary", "identity") else 0.7
            score = (
                0.45 * lexical
                + 0.20 * float(fact.confidence)
                + 0.15 * recency
                + 0.20 * category_weight
            )
            scored.append((score, fact))
        scored.sort(key=lambda pair: (pair[0], pair[1].last_confirmed), reverse=True)
        return [fact for _, fact in scored[:limit]]

    def recall_open_loops(self, query: str, character_id: str, user_id: str,
                          limit: int = 2) -> List[OpenLoop]:
        loops = self.get_open_loops(character_id, user_id, limit=30)
        query_tokens = tokenize(query)
        scored = []
        for loop in loops:
            lexical = _lexical_score(query_tokens, tokenize(loop.summary))
            score = 0.6 * lexical + 0.4 * float(loop.importance)
            scored.append((score, loop))
        scored.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        return [loop for _, loop in scored[:limit]]

    def recall(self, query: str, character_id: str, user_id: str,
               limit_cards: int = 7, limit_facts: int = 8,
               limit_loops: int = 2,
               now: Optional[float] = None) -> RecallResult:
        return RecallResult(
            cards=self.recall_cards(query, character_id, user_id,
                                    limit=limit_cards, now=now),
            facts=self.recall_facts(query, character_id, user_id,
                                    limit=limit_facts, now=now),
            relationship=self.get_relationship(character_id, user_id),
            open_loops=self.recall_open_loops(query, character_id, user_id,
                                              limit=limit_loops),
            query=query,
        )

    # ── render ──────────────────────────────────────────

    def _fact_line(self, fact: UserFact) -> str:
        if fact.key == "identity.name":
            return f"名字：{fact.value}"
        if fact.key.startswith("preference.") or fact.key.startswith("boundary."):
            return fact.value
        return f"{fact.key}：{fact.value}"

    def render_context(self, result: RecallResult, budget_chars: int = 1200,
                       now: Optional[float] = None) -> str:
        now = now or time.time()
        lines: List[str] = ["【你记得的】"]

        relationship = result.relationship
        if relationship and relationship.summary:
            lines.append(f"- 关系：{relationship.summary}")

        if result.facts:
            lines.append("- 关于对方：")
            for fact in result.facts:
                suffix = "（不太确定）" if fact.confidence < 0.7 else ""
                lines.append(f"  - {self._fact_line(fact)}{suffix}")

        if result.cards:
            lines.append("- 相关往事：")
            for card in result.cards:
                lines.append(f"  - {_relative_time(card.created_at, now)}{card.summary}")

        if result.open_loops:
            lines.append("- 还没闭环：")
            for loop in result.open_loops:
                lines.append(f"  - {loop.summary}{_format_due(loop.due_at, now)}")

        if len(lines) == 1:
            lines.append("- （暂时没有相关记忆）")

        note = "（只在自然相关时提起；不确定就说不确定；不要逐条复述。）"
        return _truncate_to_budget(lines, budget_chars, reserved_suffix=note)

    # ── maintenance ─────────────────────────────────────

    def forget_user(self, character_id: str, user_id: str) -> None:
        """彻底删除某个关系域。卡片删除会触发 FTS 清理。"""
        self.db.execute(
            "DELETE FROM memory_cards WHERE character_id=? AND user_id=?",
            (character_id, user_id),
        )
        self.db.execute(
            "DELETE FROM user_facts WHERE character_id=? AND user_id=?",
            (character_id, user_id),
        )
        self.db.execute(
            "DELETE FROM open_loops WHERE character_id=? AND user_id=?",
            (character_id, user_id),
        )
        self.db.execute(
            "DELETE FROM relationships WHERE character_id=? AND user_id=?",
            (character_id, user_id),
        )
        self.db.commit()

    def memory_bias(self, character_id: str, user_id: str,
                    baseline: Optional[Dict[str, float]] = None,
                    tau_days: float = 30.0,
                    cap: float = 0.2,
                    now: Optional[float] = None) -> Dict[str, float]:
        """把长期记忆压成一个有上限的情绪先验。

        注意：这只是先验，应该作用在 appraisal / relationship 上，
        绝不能直接写 Personality，也不应该每 tick 拉情绪状态。
        """
        now = now or time.time()
        baseline = baseline or {}
        weighted, total_weight = self._weighted_cards(
            character_id, user_id, tau_days=tau_days, now=now,
        )

        totals = {channel: 0.0 for channel in CHANNELS}
        for card, weight in weighted:
            for channel in CHANNELS:
                value = float(card.emotion_snap.get(channel, 0.0))
                base = float(baseline.get(channel, 0.0))
                totals[channel] += (value - base) * weight

        if total_weight <= 0:
            return {channel: 0.0 for channel in CHANNELS}

        return {
            channel: max(-cap, min(cap, totals[channel] / total_weight))
            for channel in CHANNELS
        }

    def stats(self, character_id: Optional[str] = None,
              user_id: Optional[str] = None) -> Dict[str, int]:
        filters: List[str] = []
        params: List[Any] = []
        if character_id:
            filters.append("character_id=?")
            params.append(character_id)
        if user_id:
            filters.append("user_id=?")
            params.append(user_id)
        where = (" WHERE " + " AND ".join(filters)) if filters else ""

        cards = self.db.execute(
            f"SELECT COUNT(*) FROM memory_cards{where}", params
        ).fetchone()[0]
        facts = self.db.execute(
            f"SELECT COUNT(*) FROM user_facts{where} AND status='active'"
            if where else
            "SELECT COUNT(*) FROM user_facts WHERE status='active'",
            params,
        ).fetchone()[0]
        loops = self.db.execute(
            f"SELECT COUNT(*) FROM open_loops{where} AND status='open'"
            if where else
            "SELECT COUNT(*) FROM open_loops WHERE status='open'",
            params,
        ).fetchone()[0]
        relationships = self.db.execute(
            f"SELECT COUNT(*) FROM relationships{where}", params
        ).fetchone()[0]
        return {
            "cards": int(cards),
            "facts": int(facts),
            "open_loops": int(loops),
            "relationships": int(relationships),
        }

    def close(self) -> None:
        self.db.close()


# ══════════════════════════════════════════════════════
# 轻量抽取器（规则版；后续可替换成 LLM）
# ══════════════════════════════════════════════════════

class MemoryExtractor:
    """从用户话语里抽取「值得记住」的候选。

    v0.1 只做高精度规则，不做开放式事件抽取：
      - 身份 / 偏好 / 边界 / 承诺 / 明确要求记住
    复杂事件和摘要交给 MemoryService 的显式 remember_event
    或后续 LLM extractor。
    """

    _PATTERNS = [
        ("note", re.compile(r"(?:记住|记一下|帮我记住|别忘了)[：:，,\s]*(.{2,60})")),
        ("identity.name", re.compile(r"我叫([^\s，。！？,!?；;]{1,20})")),
        ("preference.like", re.compile(
            r"我(?:很|挺|特别)?(?:喜欢|偏好|爱吃|爱看|爱听)"
            r"([^，。！？,!?；;]{1,30})"
        )),
        ("preference.dislike", re.compile(
            r"我(?:很|挺|特别)?(?:不喜欢|讨厌)"
            r"([^，。！？,!?；;]{1,30})"
        )),
        ("boundary", re.compile(
            r"(?:不要|我不接受|我受不了|你别|请别|别再)"
            r"([^，。！？,!?；;]{1,30})"
        )),
        ("promise", re.compile(
            r"(?:我答应|我保证|我承诺)([^，。！？,!?；;]{1,40})"
        )),
        ("open_loop", re.compile(
            r"(?:提醒我|下次)([^，。！？,!?；;]{1,40})"
        )),
    ]

    def extract(self, text: str) -> List[MemoryCandidate]:
        text = (text or "").strip()
        if not text:
            return []

        candidates: List[MemoryCandidate] = []
        seen = set()

        for label, pattern in self._PATTERNS:
            for match in pattern.finditer(text):
                captured = match.group(1).strip("，。！？,.!? ")
                if not captured:
                    continue

                if label == "note":
                    candidate = MemoryCandidate(
                        kind="note",
                        summary=text[:120],
                        confidence=0.9,
                        tags=["explicit"],
                    )
                elif label == "identity.name":
                    candidate = MemoryCandidate(
                        kind="fact",
                        summary=f"名字：{captured}",
                        fact_key="identity.name",
                        fact_value=captured,
                        category="identity",
                        confidence=0.9,
                        tags=["identity"],
                    )
                elif label == "preference.like":
                    candidate = MemoryCandidate(
                        kind="preference",
                        summary=f"喜欢{captured}",
                        fact_key=f"preference.like.{captured[:24]}",
                        fact_value=f"喜欢{captured}",
                        category="preference",
                        confidence=0.8,
                        tags=["preference"],
                    )
                elif label == "preference.dislike":
                    candidate = MemoryCandidate(
                        kind="preference",
                        summary=f"不喜欢{captured}",
                        fact_key=f"preference.dislike.{captured[:24]}",
                        fact_value=f"不喜欢{captured}",
                        category="preference",
                        confidence=0.85,
                        tags=["preference"],
                    )
                elif label == "boundary":
                    candidate = MemoryCandidate(
                        kind="boundary",
                        summary=f"不要{captured}",
                        fact_key=f"boundary.{captured[:24]}",
                        fact_value=f"不要{captured}",
                        category="boundary",
                        confidence=0.85,
                        tags=["boundary"],
                    )
                elif label == "promise":
                    candidate = MemoryCandidate(
                        kind="promise",
                        summary=f"答应{captured}",
                        confidence=0.85,
                        tags=["promise"],
                    )
                else:  # open_loop
                    candidate = MemoryCandidate(
                        kind="promise",
                        summary=captured,
                        confidence=0.7,
                        tags=["open_loop"],
                    )

                dedupe_key = (candidate.kind, candidate.fact_key or candidate.summary)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                candidates.append(candidate)

        return candidates


# ══════════════════════════════════════════════════════
# 写入闸门 v2
# ══════════════════════════════════════════════════════

class MemoryWriteGate:
    """写入前的确定性闸门：去重、冲突、时效、置信度。

    v0.1 只做规则判断，不调用模型：
      - explicit / boundary / identity 高优先；
      - 低置信度候选丢弃；
      - 同一 key 的新事实允许 supersede；
      - 近期卡片摘要 Jaccard 过高视为重复；
      - 开放循环同样去重。
    """

    def __init__(self, min_confidence: float = 0.55,
                 dedupe_days: float = 7.0,
                 similarity_threshold: float = 0.8):
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.dedupe_days = max(0.0, float(dedupe_days))
        self.similarity_threshold = max(
            0.0, min(1.0, float(similarity_threshold)),
        )

    def decide(self, candidate: MemoryCandidate,
               store: RelationshipMemoryStore,
               character_id: str, user_id: str,
               now: Optional[float] = None) -> GateDecision:
        now = now or time.time()
        tags = set(candidate.tags or [])

        # 明确要求记住 / 边界 / 身份：即使置信度略低也保留。
        if "explicit" in tags or candidate.kind == "note":
            return GateDecision(True, "explicit", "card")
        if candidate.category in ("identity", "boundary"):
            if candidate.confidence < 0.5:
                return GateDecision(False, "low_confidence", "skip")
            return GateDecision(True, "high_priority", "write")

        if candidate.confidence < self.min_confidence:
            return GateDecision(False, "low_confidence", "skip")

        if candidate.kind == "promise":
            query_tokens = tokenize(candidate.summary)
            for loop in store.get_open_loops(character_id, user_id, limit=30):
                if _jaccard(
                    query_tokens, tokenize(loop.summary),
                ) >= self.similarity_threshold:
                    return GateDecision(
                        False, "duplicate_open_loop", "skip",
                    )
            return GateDecision(True, "commitment", "open_loop")

        if candidate.fact_key:
            existing = store.get_facts(character_id, user_id)
            for fact in existing:
                if fact.key != _normalize_key(candidate.fact_key):
                    continue
                if fact.value.strip() == (
                    candidate.fact_value or candidate.summary
                ).strip():
                    return GateDecision(
                        True, "confirm_existing", "fact",
                    )
                return GateDecision(
                    True, "supersede_existing", "fact",
                )
            return GateDecision(True, "new_fact", "fact")

        candidate_tokens = tokenize(candidate.summary)
        for card in store.recent_cards(
            character_id, user_id, days=self.dedupe_days, now=now,
        ):
            similarity = _jaccard(
                candidate_tokens, tokenize(card.summary),
            )
            if similarity >= self.similarity_threshold:
                return GateDecision(
                    False, "duplicate_card", "skip",
                )

        return GateDecision(True, "novel_card", "card")


# ══════════════════════════════════════════════════════
# Service
# ══════════════════════════════════════════════════════

class MemoryService:
    """把「抽取 → 入库 → 召回 → prompt 区块」串起来。"""

    def __init__(self, store: Optional[RelationshipMemoryStore] = None,
                 extractor: Optional[MemoryExtractor] = None,
                 gate: Optional[MemoryWriteGate] = None,
                 auto_summary: bool = True,
                 max_writes_per_turn: int = 4):
        self.store = store or RelationshipMemoryStore()
        self.extractor = extractor or MemoryExtractor()
        self.gate = gate or MemoryWriteGate()
        self.auto_summary = bool(auto_summary)
        self.max_writes_per_turn = max(1, int(max_writes_per_turn))
        self.last_gate_decisions: List[Dict[str, str]] = []

    def remember_turn(self, character_id: str, user_id: str, user_text: str,
                      assistant_text: str = "",
                      appraisal: Any = None,
                      emotion_snap: Any = None,
                      raw_ref: Optional[str] = None,
                      now: Optional[float] = None) -> List[Dict[str, Any]]:
        """回复之后异步调用。只存抽取到的候选，不存整段对话。"""
        now = now or time.time()
        candidates = self.extractor.extract(user_text)
        appraisal_dict = _as_dict(appraisal)
        emotion_dict = _as_dict(emotion_snap)
        salience = _salience_from_emotion(emotion_dict)
        stored: List[Dict[str, Any]] = []
        self.last_gate_decisions = []
        accepted = 0

        for candidate in candidates:
            if accepted >= self.max_writes_per_turn:
                self.last_gate_decisions.append({
                    "summary": candidate.summary,
                    "reason": "turn_budget",
                    "action": "skip",
                })
                continue

            decision = self.gate.decide(
                candidate, self.store, character_id, user_id, now=now,
            )
            self.last_gate_decisions.append({
                "summary": candidate.summary,
                "reason": decision.reason,
                "action": decision.action,
            })
            if not decision.accept:
                continue
            accepted += 1

            if candidate.kind == "promise":
                loop_id = self.store.add_open_loop(
                    candidate.summary,
                    character_id=character_id,
                    user_id=user_id,
                    importance=max(0.6, salience),
                )
                stored.append({"type": "open_loop", "id": loop_id})
                continue

            if candidate.fact_key:
                fact_id = self.store.upsert_fact(
                    character_id=character_id,
                    user_id=user_id,
                    key=candidate.fact_key,
                    value=candidate.fact_value or candidate.summary,
                    category=candidate.category,
                    confidence=candidate.confidence,
                    evidence=[raw_ref] if raw_ref else [],
                    now=now,
                )
                stored.append({"type": "fact", "id": fact_id})
                continue

            card_id = self.store.add_card(MemoryCard(
                summary=candidate.summary,
                character_id=character_id,
                user_id=user_id,
                kind=candidate.kind,
                tags=candidate.tags,
                appraisal=appraisal_dict,
                emotion_snap=emotion_dict,
                salience=salience,
                confidence=candidate.confidence,
                source="user_said",
                raw_ref=raw_ref,
                created_at=now,
            ))
            stored.append({"type": "card", "id": card_id})

        if stored and self.auto_summary:
            self.refresh_summary(character_id, user_id, now=now)
        return stored

    def remember_event(self, character_id: str, user_id: str, summary: str,
                       kind: str = "episode",
                       tags: Optional[Sequence[str]] = None,
                       appraisal: Any = None,
                       emotion_snap: Any = None,
                       salience: float = 0.6,
                       confidence: float = 0.85,
                       source: str = "observed",
                       raw_ref: Optional[str] = None,
                       created_at: Optional[float] = None,
                       now: Optional[float] = None) -> int:
        created_at = created_at or now or time.time()
        card_id = self.store.add_card(MemoryCard(
            summary=summary,
            character_id=character_id,
            user_id=user_id,
            kind=kind,
            tags=list(tags or []),
            appraisal=_as_dict(appraisal),
            emotion_snap=_as_dict(emotion_snap),
            salience=salience,
            confidence=confidence,
            source=source,
            raw_ref=raw_ref,
            created_at=created_at,
        ))
        if self.auto_summary:
            self.refresh_summary(character_id, user_id, now=created_at)
        return card_id

    def appraisal_prior(self, character_id: str, user_id: str,
                        tau_days: float = 30.0, cap: float = 0.2,
                        now: Optional[float] = None) -> Dict[str, float]:
        """L4：直接喂给 engine.tick(appraisal_bias=...)。"""
        return self.store.appraisal_prior(
            character_id, user_id, tau_days=tau_days, cap=cap, now=now,
        )

    def prior(self, character_id: str, user_id: str,
              baseline: Optional[Dict[str, float]] = None,
              tau_days: float = 30.0, cap: float = 0.2,
              now: Optional[float] = None) -> MemoryPrior:
        return self.store.memory_prior(
            character_id, user_id, baseline=baseline,
            tau_days=tau_days, cap=cap, now=now,
        )

    def refresh_summary(self, character_id: str, user_id: str,
                        max_chars: int = 160,
                        now: Optional[float] = None) -> str:
        if not self.auto_summary:
            return self.store.get_relationship(
                character_id, user_id,
            ).summary
        return self.store.update_relationship_summary(
            character_id, user_id, max_chars=max_chars, now=now,
        )

    def build_context(self, character_id: str, user_id: str, query: str,
                      budget_chars: int = 1200) -> str:
        result = self.store.recall(query, character_id, user_id)
        return self.store.render_context(result, budget_chars=budget_chars)

    def forget(self, character_id: str, user_id: str) -> None:
        self.store.forget_user(character_id, user_id)


# ══════════════════════════════════════════════════════
# 旧 MemoryStoreDB 迁移
# ══════════════════════════════════════════════════════

def migrate_legacy_memories(legacy_db_path: str,
                            store: RelationshipMemoryStore,
                            character_id: str, user_id: str,
                            skip_automatic: bool = True,
                            now: Optional[float] = None) -> Dict[str, int]:
    """把旧 engine.MemoryStoreDB 的 memories 表迁进关系域记忆。

    旧库没有 character_id / user_id 维度，所以必须显式指定归属。
    迁移是可重复执行的：raw_ref 使用 legacy:<绝对路径>:<id> 去重。
    """
    now = now or time.time()
    stats = {
        "seen": 0,
        "migrated": 0,
        "skipped_empty": 0,
        "skipped_automatic": 0,
        "skipped_duplicate": 0,
    }
    if not os.path.exists(legacy_db_path):
        return stats

    conn = sqlite3.connect(legacy_db_path)
    conn.row_factory = sqlite3.Row
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()
        if not table:
            return stats

        rows = conn.execute(
            """SELECT id, content, tier, arousal, relevance, emotion_snap,
                      timestamp, access_count
               FROM memories ORDER BY id ASC"""
        ).fetchall()
        legacy_ref = os.path.abspath(legacy_db_path)

        for row in rows:
            stats["seen"] += 1
            content = (row["content"] or "").strip()
            if not content:
                stats["skipped_empty"] += 1
                continue
            if skip_automatic and content.startswith("gc="):
                stats["skipped_automatic"] += 1
                continue

            raw_ref = f"legacy:{legacy_ref}:{row['id']}"
            if store.has_card_raw_ref(character_id, user_id, raw_ref):
                stats["skipped_duplicate"] += 1
                continue

            emotion_snap = _deserialize(row["emotion_snap"], {})
            if not isinstance(emotion_snap, dict):
                emotion_snap = {}
            emotion_snap = {
                str(key): float(value)
                for key, value in emotion_snap.items()
                if isinstance(value, (int, float))
            }
            salience = _salience_from_emotion(emotion_snap)
            if not emotion_snap:
                salience = _clamp(float(row["arousal"] or 0.0), 0.2, 1.0)

            tier = row["tier"] or "short_term"
            kind = {
                "flash": "milestone",
                "long_term": "episode",
            }.get(tier, "note")
            store.add_card(MemoryCard(
                summary=content[:160],
                character_id=character_id,
                user_id=user_id,
                kind=kind,
                tags=["legacy", f"tier:{tier}"],
                emotion_snap=emotion_snap,
                salience=salience,
                confidence=0.65 if tier != "short_term" else 0.55,
                source="observed",
                raw_ref=raw_ref,
                created_at=float(row["timestamp"] or now),
                access_count=int(row["access_count"] or 0),
            ))
            stats["migrated"] += 1
    finally:
        conn.close()
    return stats


# ══════════════════════════════════════════════════════
# Demo
# ══════════════════════════════════════════════════════

def _demo() -> None:
    store = RelationshipMemoryStore(":memory:")
    service = MemoryService(store)

    service.remember_turn(
        "furina", "alice",
        "我叫小枫。我最近在赶一个项目，周五要汇报。我不喜欢被说教。",
        raw_ref="turn_001",
    )
    service.remember_event(
        "furina", "alice",
        "上周项目失败，她来倾诉；你没有给建议，只是陪着。她说这样就好。",
        kind="episode",
        tags=["工作", "失败", "倾诉"],
        emotion_snap={"sadness": 0.7, "trust": 0.6, "love": 0.5},
        salience=0.85,
        raw_ref="turn_000",
    )
    service.store.add_open_loop(
        "周五项目汇报", character_id="furina", user_id="alice",
        importance=0.9,
    )
    service.store.update_relationship(
        "furina", "alice",
        summary="认识三个月，从客气到会开玩笑；她不喜欢被说教。",
    )

    print(service.build_context(
        "furina", "alice", "今天又被老板骂了，烦死了。",
    ))
    print("\nstats:", store.stats("furina", "alice"))
    store.close()


if __name__ == "__main__":
    _demo()
