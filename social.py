"""L8 社交场景：角色间关系持久化 + 有向感染编排。

这一层不实现新的情绪动力学，也不进入 ``tick()``。它只做两件事：

1. 维护"角色 A 对角色 B"的有向关系快照（trust / love / familiarity /
   tension / boundaries），并可选持久化到 SQLite；
2. 在场景回合里，用 ``contagion`` 的纯函数把发言者的 appraisal 变成
   听者的有界偏置，并在回合结束后按听者的 appraisal 更新关系。

硬边界：

- 不共享 ``state`` / ``memory`` / ``scars`` / ``tolerance``；
- 不写 ``Personality`` / ``CharacterCore``；
- 默认单角色路径不导入本模块，因此默认 tick 与 golden 不变；
- 没有第三方依赖。
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from contagion import (
    DEFAULT_CONTAGION_CAP,
    DEFAULT_CONTAGION_GAIN,
    DEFAULT_NETWORK_HOP_DECAY,
    DEFAULT_NETWORK_MAX_HOPS,
    ContagionEdge,
    ContagionNetwork,
    DirectionalRelationship,
    directional_relationship_strength,
)
from proactive import ProactiveConfig, ProactiveScheduler

SOCIAL_SCHEMA_VERSION = 1
DEFAULT_WORLD_ID = "default"
DEFAULT_TRUST = 0.2
DEFAULT_LOVE = 0.2
DEFAULT_FAMILIARITY = 0.0
DEFAULT_TENSION = 0.0
MAX_TRUST = 1.0
MAX_LOVE = 2.0
MAX_FAMILIARITY = 1.0
MAX_TENSION = 1.0
DEFAULT_TENSION_PENALTY = 0.25
_EPSILON = 1e-9

_ISOLATED_ATTRIBUTES = ("state", "memory", "scars", "tolerance")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return number


def _appraisal_value(appraisal: Any, name: str, default: float) -> float:
    if appraisal is None:
        return float(default)
    if isinstance(appraisal, Mapping):
        raw = appraisal.get(name, default)
    else:
        raw = getattr(appraisal, name, default)
    return _finite_float(raw, default)


def _normalize_boundaries(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        raw = value.replace("，", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = []
    boundaries = []
    for item in raw:
        text = str(item).strip()
        if text and text not in boundaries:
            boundaries.append(text)
        if len(boundaries) >= 20:
            break
    return tuple(boundaries)


@dataclass
class PeerRelationship:
    """角色 source 对角色 target 的单向关系快照。

    ``tension`` 是这里的扩展：关系越紧张，感染边越弱。它只影响
    ``FriendScene`` 的边权与关系面板，不写引擎状态或人格。
    """

    world_id: str = DEFAULT_WORLD_ID
    source_id: str = "A"
    target_id: str = "B"
    trust: float = DEFAULT_TRUST
    love: float = DEFAULT_LOVE
    familiarity: float = DEFAULT_FAMILIARITY
    tension: float = DEFAULT_TENSION
    boundaries: Tuple[str, ...] = ()
    summary: str = ""
    interactions: int = 0
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not isinstance(self.world_id, str) or not self.world_id:
            raise ValueError("world_id must be a non-empty string")
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("source_id must be a non-empty string")
        if not isinstance(self.target_id, str) or not self.target_id:
            raise ValueError("target_id must be a non-empty string")
        if self.source_id == self.target_id:
            raise ValueError("peer relationship cannot be a self-loop")
        self.trust = _clamp(_finite_float(self.trust, DEFAULT_TRUST),
                            0.0, MAX_TRUST)
        self.love = _clamp(_finite_float(self.love, DEFAULT_LOVE),
                           0.0, MAX_LOVE)
        self.familiarity = _clamp(
            _finite_float(self.familiarity, DEFAULT_FAMILIARITY),
            0.0, MAX_FAMILIARITY,
        )
        self.tension = _clamp(
            _finite_float(self.tension, DEFAULT_TENSION),
            0.0, MAX_TENSION,
        )
        self.boundaries = _normalize_boundaries(self.boundaries)
        self.interactions = max(0, int(_finite_float(self.interactions, 0)))

    def to_directional(self) -> DirectionalRelationship:
        return DirectionalRelationship(
            trust=self.trust,
            love=self.love,
            familiarity=self.familiarity,
            boundaries=self.boundaries,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "world_id": self.world_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "trust": round(self.trust, 6),
            "love": round(self.love, 6),
            "familiarity": round(self.familiarity, 6),
            "tension": round(self.tension, 6),
            "boundaries": list(self.boundaries),
            "summary": self.summary,
            "interactions": self.interactions,
            "updated_at": self.updated_at,
        }


class PeerRelationshipStore:
    """角色间关系的 SQLite 存储。

    作用域是 ``(world_id, source_id, target_id)``。``world_id`` 用来隔离
    不同的朋友群 / 剧本；同一对角色在另一个 world 不会读到旧关系。
    默认使用内存库，方便测试；生产调用方传入文件路径即可持久化。
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self.db = sqlite3.connect(db_path, timeout=8.0)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
        except Exception:
            self.db.close()
            raise

    def _init_schema(self) -> None:
        current = int(self.db.execute("PRAGMA user_version").fetchone()[0])
        if current > SOCIAL_SCHEMA_VERSION:
            raise RuntimeError(
                "social schema is newer than this code: "
                f"db={current}, code={SOCIAL_SCHEMA_VERSION}"
            )
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS peer_relationships (
                world_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                trust REAL NOT NULL DEFAULT 0.2,
                love REAL NOT NULL DEFAULT 0.2,
                familiarity REAL NOT NULL DEFAULT 0.0,
                tension REAL NOT NULL DEFAULT 0.0,
                boundaries TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL DEFAULT '',
                interactions INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(world_id, source_id, target_id)
            );
            CREATE INDEX IF NOT EXISTS idx_peer_world
                ON peer_relationships(world_id, source_id, target_id);
        """)
        self.db.execute(f"PRAGMA user_version = {SOCIAL_SCHEMA_VERSION}")
        self.db.commit()

    def get(self, world_id: str, source_id: str, target_id: str
            ) -> PeerRelationship:
        row = self.db.execute(
            """
            SELECT world_id, source_id, target_id, trust, love, familiarity,
                   tension, boundaries, summary, interactions, updated_at
            FROM peer_relationships
            WHERE world_id = ? AND source_id = ? AND target_id = ?
            """,
            (world_id, source_id, target_id),
        ).fetchone()
        if row is None:
            return PeerRelationship(
                world_id=world_id, source_id=source_id, target_id=target_id,
            )
        try:
            boundaries = tuple(json.loads(row["boundaries"] or "[]"))
        except (TypeError, ValueError):
            boundaries = ()
        return PeerRelationship(
            world_id=row["world_id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            trust=row["trust"],
            love=row["love"],
            familiarity=row["familiarity"],
            tension=row["tension"],
            boundaries=boundaries,
            summary=row["summary"],
            interactions=row["interactions"],
            updated_at=row["updated_at"],
        )

    def upsert(self, relationship: PeerRelationship) -> None:
        self.db.execute(
            """
            INSERT INTO peer_relationships (
                world_id, source_id, target_id, trust, love, familiarity,
                tension, boundaries, summary, interactions, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(world_id, source_id, target_id) DO UPDATE SET
                trust = excluded.trust,
                love = excluded.love,
                familiarity = excluded.familiarity,
                tension = excluded.tension,
                boundaries = excluded.boundaries,
                summary = excluded.summary,
                interactions = excluded.interactions,
                updated_at = excluded.updated_at
            """,
            (
                relationship.world_id,
                relationship.source_id,
                relationship.target_id,
                relationship.trust,
                relationship.love,
                relationship.familiarity,
                relationship.tension,
                json.dumps(list(relationship.boundaries), ensure_ascii=False),
                relationship.summary,
                relationship.interactions,
                relationship.updated_at,
            ),
        )
        self.db.commit()

    def all(self, world_id: str) -> Dict[Tuple[str, str], PeerRelationship]:
        rows = self.db.execute(
            """
            SELECT world_id, source_id, target_id, trust, love, familiarity,
                   tension, boundaries, summary, interactions, updated_at
            FROM peer_relationships
            WHERE world_id = ?
            """,
            (world_id,),
        ).fetchall()
        result: Dict[Tuple[str, str], PeerRelationship] = {}
        for row in rows:
            try:
                boundaries = tuple(json.loads(row["boundaries"] or "[]"))
            except (TypeError, ValueError):
                boundaries = ()
            rel = PeerRelationship(
                world_id=row["world_id"],
                source_id=row["source_id"],
                target_id=row["target_id"],
                trust=row["trust"],
                love=row["love"],
                familiarity=row["familiarity"],
                tension=row["tension"],
                boundaries=boundaries,
                summary=row["summary"],
                interactions=row["interactions"],
                updated_at=row["updated_at"],
            )
            result[(rel.source_id, rel.target_id)] = rel
        return result

    def delete_world(self, world_id: str) -> int:
        cursor = self.db.execute(
            "DELETE FROM peer_relationships WHERE world_id = ?",
            (world_id,),
        )
        self.db.commit()
        return int(cursor.rowcount or 0)

    def close(self) -> None:
        self.db.close()


@dataclass
class SceneActor:
    """场景中的一个角色；引擎必须与其它角色隔离。"""

    actor_id: str
    name: str
    engine: Any
    character_id: str = "default"

    def __post_init__(self) -> None:
        if not isinstance(self.actor_id, str) or not self.actor_id:
            raise ValueError("actor_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")
        if not callable(getattr(self.engine, "tick", None)):
            raise TypeError(f"actor {self.actor_id!r} engine must provide tick()")
        if not isinstance(self.character_id, str) or not self.character_id:
            raise ValueError("character_id must be a non-empty string")


class FriendScene:
    """2–N 个角色的社交场景。

    ``FriendScene`` 只负责编排：

    - ``biases_for(source_id, source_appraisal)`` 生成听者偏置；
    - ``record_turn(source_id, appraisals)`` 更新听者 → 发言者的关系；
    - ``step(...)`` 是库内测试用的完整 tick 版本。

    仪表盘可以先用 ``biases_for`` 拿到偏置，再自己调用 ``respond()``，
    最后调用 ``record_turn()``，这样既能生成台词，也不会重复 tick。
    """

    def __init__(self, actors: Iterable[SceneActor], *,
                 world_id: str = DEFAULT_WORLD_ID,
                 store: Optional[PeerRelationshipStore] = None,
                 gain: float = DEFAULT_CONTAGION_GAIN,
                 cap: float = DEFAULT_CONTAGION_CAP,
                 hop_decay: float = DEFAULT_NETWORK_HOP_DECAY,
                 max_hops: int = DEFAULT_NETWORK_MAX_HOPS,
                 tension_penalty: float = DEFAULT_TENSION_PENALTY,
                 proactive_config: Optional[ProactiveConfig] = None,
                 proactive_now: Optional[float] = None) -> None:
        if not isinstance(world_id, str) or not world_id:
            raise ValueError("world_id must be a non-empty string")
        self.world_id = world_id
        self._owns_store = store is None
        self.store = store or PeerRelationshipStore(":memory:")
        self.actors: Dict[str, SceneActor] = {}
        for actor in actors:
            if not isinstance(actor, SceneActor):
                raise TypeError("actors must be SceneActor instances")
            if actor.actor_id in self.actors:
                raise ValueError(f"duplicate actor_id: {actor.actor_id!r}")
            self.actors[actor.actor_id] = actor
        if not self.actors:
            raise ValueError("FriendScene requires at least one actor")

        self._validate_isolation()
        self.gain = float(gain)
        self.cap = float(cap)
        self.hop_decay = float(hop_decay)
        self.max_hops = int(max_hops)
        self.tension_penalty = _clamp(
            _finite_float(tension_penalty, DEFAULT_TENSION_PENALTY),
            0.0, 1.0,
        )
        self.engines = {
            actor_id: actor.engine for actor_id, actor in self.actors.items()
        }
        self.relationships = self.store.all(world_id)
        self.turn = 0
        self.last_turn: Dict[str, Any] = {}
        self.proactive = ProactiveScheduler(
            self.actors,
            self.relationship,
            config=proactive_config,
            now=proactive_now,
        )

    def _validate_isolation(self) -> None:
        actors = list(self.actors.values())
        for index, actor in enumerate(actors):
            for other in actors[index + 1:]:
                if actor.engine is other.engine:
                    raise ValueError(
                        f"actors {actor.actor_id!r} and {other.actor_id!r} "
                        "must use different engines"
                    )
                for attribute in _ISOLATED_ATTRIBUTES:
                    if not hasattr(actor.engine, attribute):
                        continue
                    if not hasattr(other.engine, attribute):
                        continue
                    if getattr(actor.engine, attribute) is getattr(
                        other.engine, attribute,
                    ):
                        raise ValueError(
                            "scene actors must not share "
                            f"{attribute}: {actor.actor_id!r}, "
                            f"{other.actor_id!r}"
                        )

    def _require_actor(self, actor_id: str) -> SceneActor:
        actor = self.actors.get(actor_id)
        if actor is None:
            raise KeyError(f"unknown actor: {actor_id!r}")
        return actor

    def relationship(self, source_id: str, target_id: str) -> PeerRelationship:
        self._require_actor(source_id)
        self._require_actor(target_id)
        if source_id == target_id:
            raise ValueError("peer relationship cannot be a self-loop")
        return self.relationships.get(
            (source_id, target_id),
            PeerRelationship(
                world_id=self.world_id,
                source_id=source_id,
                target_id=target_id,
            ),
        )

    def set_relationship(self, source_id: str, target_id: str, *,
                         trust: Optional[float] = None,
                         love: Optional[float] = None,
                         familiarity: Optional[float] = None,
                         tension: Optional[float] = None,
                         boundaries: Any = None,
                         summary: Optional[str] = None,
                         now: Optional[float] = None) -> PeerRelationship:
        rel = self.relationship(source_id, target_id)
        if trust is not None:
            rel.trust = _clamp(_finite_float(trust, rel.trust), 0.0, MAX_TRUST)
        if love is not None:
            rel.love = _clamp(_finite_float(love, rel.love), 0.0, MAX_LOVE)
        if familiarity is not None:
            rel.familiarity = _clamp(
                _finite_float(familiarity, rel.familiarity),
                0.0, MAX_FAMILIARITY,
            )
        if tension is not None:
            rel.tension = _clamp(
                _finite_float(tension, rel.tension), 0.0, MAX_TENSION,
            )
        if boundaries is not None:
            rel.boundaries = _normalize_boundaries(boundaries)
        if summary is not None:
            rel.summary = str(summary)
        rel.updated_at = time.time() if now is None else float(now)
        self.relationships[(source_id, target_id)] = rel
        self.store.upsert(rel)
        return rel

    def edges(self) -> Tuple[ContagionEdge, ...]:
        """用当前关系快照生成有向感染边。

        边权先用 ``directional_relationship_strength`` 计算 trust / love /
        familiarity / boundaries；再乘 ``1 - tension_penalty * tension``。
        """
        edges = []
        for source_id in self.actors:
            for target_id in self.actors:
                if source_id == target_id:
                    continue
                rel = self.relationship(source_id, target_id)
                base = directional_relationship_strength(rel.to_directional())
                strength = base * (1.0 - self.tension_penalty * rel.tension)
                strength = _clamp(strength, 0.0, 1.0)
                if strength > _EPSILON:
                    edges.append(ContagionEdge(
                        source_id, target_id, strength=strength,
                    ))
        return tuple(edges)

    def set_proactive_config(self, config: Any) -> ProactiveConfig:
        """替换主动联系调度参数；不重置计时与计数。"""
        return self.proactive.set_config(config)

    def _network(self) -> ContagionNetwork:
        return ContagionNetwork(
            self.engines,
            self.edges(),
            gain=self.gain,
            cap=self.cap,
            hop_decay=self.hop_decay,
            max_hops=self.max_hops,
        )

    def biases_for(self, source_id: str, source_appraisal: Any, *,
                   relevance: Optional[Mapping[str, float]] = None
                   ) -> Dict[str, Dict[str, float]]:
        """返回本轮每个听者应收到的四维感染偏置。

        ``source_id`` 必须是场景角色；用户发言不属于角色间感染，返回空。
        ``relevance`` 是可选的目标相关性门控（0–1），用于后续接入
        "这件事和 B 有多相关"；默认 1.0。
        """
        if source_id not in self.actors or source_appraisal is None:
            return {}
        biases = self._network().derive({source_id: source_appraisal})
        if not biases:
            return {}
        if not isinstance(relevance, Mapping):
            return biases

        gated: Dict[str, Dict[str, float]] = {}
        for target_id, bias in biases.items():
            factor = _clamp(
                _finite_float(relevance.get(target_id, 1.0), 1.0),
                0.0, 1.0,
            )
            if factor <= _EPSILON:
                continue
            scaled = {
                dimension: value * factor
                for dimension, value in bias.items()
                if abs(value * factor) > _EPSILON
            }
            if scaled:
                gated[target_id] = scaled
        return gated

    def _relationship_delta(self, appraisal: Any,
                            boundary_violation: bool = False
                            ) -> Dict[str, float]:
        relevance = _clamp(
            _appraisal_value(appraisal, "goal_relevance", 0.0), 0.0, 1.0,
        )
        valence = _clamp(
            0.5 * _appraisal_value(appraisal, "goal_conduciveness", 0.0)
            + 0.5 * _appraisal_value(appraisal, "social_evaluation", 0.0),
            -1.0, 1.0,
        )
        trust_delta = 0.02 * valence
        love_delta = 0.005 * max(0.0, valence)
        familiarity_delta = 0.004 + 0.006 * relevance
        tension_delta = 0.04 * max(0.0, -valence)
        if boundary_violation:
            trust_delta -= 0.05
            tension_delta += 0.08
        return {
            "trust": trust_delta,
            "love": love_delta,
            "familiarity": familiarity_delta,
            "tension": tension_delta,
        }

    def record_turn(self, source_id: str,
                    appraisals: Optional[Mapping[str, Any]] = None, *,
                    boundary_violations: Optional[Mapping[str, bool]] = None,
                    now: Optional[float] = None) -> Dict[str, Any]:
        """按听者对发言的 appraisal 更新"听者 → 发言者"的关系。

        ``source_id`` 可以是角色 id，也可以是 ``"user"``。用户发言不更新
        角色间关系，但仍算一次场景回合。
        """
        appraisals = appraisals if isinstance(appraisals, Mapping) else {}
        boundary_violations = (
            boundary_violations
            if isinstance(boundary_violations, Mapping) else {}
        )
        timestamp = time.time() if now is None else float(now)
        changes: Dict[str, Any] = {}

        if source_id in self.actors:
            for target_id in self.actors:
                if target_id == source_id:
                    continue
                appraisal = appraisals.get(target_id)
                if appraisal is None:
                    continue
                rel = self.relationship(target_id, source_id)
                delta = self._relationship_delta(
                    appraisal,
                    boundary_violation=bool(
                        boundary_violations.get(target_id, False)
                    ),
                )
                rel.trust = _clamp(
                    rel.trust + delta["trust"], 0.0, MAX_TRUST,
                )
                rel.love = _clamp(
                    rel.love + delta["love"], 0.0, MAX_LOVE,
                )
                rel.familiarity = _clamp(
                    rel.familiarity + delta["familiarity"],
                    0.0, MAX_FAMILIARITY,
                )
                rel.tension = _clamp(
                    rel.tension + delta["tension"], 0.0, MAX_TENSION,
                )
                rel.interactions += 1
                rel.updated_at = timestamp
                rel.summary = (
                    f"trust {rel.trust:.2f} · tension {rel.tension:.2f} · "
                    f"{rel.interactions} 次互动"
                )
                self.relationships[(target_id, source_id)] = rel
                self.store.upsert(rel)
                changes[target_id] = {
                    "target_id": target_id,
                    "source_id": source_id,
                    "delta": {key: round(value, 6)
                              for key, value in delta.items()},
                    "relationship": rel.to_dict(),
                }

        self.proactive.observe_turn(source_id, now=timestamp)
        self.turn += 1
        self.last_turn = {
            "turn": self.turn,
            "source": source_id,
            "relationship_changes": changes,
        }
        return self.last_turn

    def step(self, source_id: str,
             appraisals: Optional[Mapping[str, Any]] = None, *,
             relevance: Optional[Mapping[str, float]] = None,
             boundary_violations: Optional[Mapping[str, bool]] = None,
             now: Optional[float] = None,
             trace: bool = False) -> Dict[str, Any]:
        """完整跑一个场景回合：算偏置 → tick 所有角色 → 更新关系。"""
        appraisals = appraisals if isinstance(appraisals, Mapping) else {}
        boundary_violations = (
            boundary_violations
            if isinstance(boundary_violations, Mapping) else {}
        )
        biases = self.biases_for(
            source_id, appraisals.get(source_id), relevance=relevance,
        )
        results: Dict[str, Dict] = {}
        for actor_id, actor in self.actors.items():
            kwargs: Dict[str, Any] = {
                "boundary_violation": bool(
                    boundary_violations.get(actor_id, False)
                ),
                "now": now,
                "trace": trace,
            }
            bias = biases.get(actor_id)
            if bias:
                kwargs["contagion_bias"] = bias
            results[actor_id] = actor.engine.tick(
                appraisals.get(actor_id), **kwargs,
            )

        turn = self.record_turn(
            source_id, appraisals,
            boundary_violations=boundary_violations,
            now=now,
        )
        return {
            "turn": turn["turn"],
            "source": source_id,
            "biases": biases,
            "actors": results,
            "relationship_changes": turn["relationship_changes"],
        }

    def snapshot(self) -> Dict[str, Any]:
        actors = {}
        for actor_id, actor in self.actors.items():
            state = actor.engine.state
            raw = state.to_dict()
            felt = state.felt_all()
            dominant = (
                max(felt, key=lambda key: (felt[key], key))
                if felt else "neutral"
            )
            actors[actor_id] = {
                "actor_id": actor_id,
                "name": actor.name,
                "character_id": actor.character_id,
                "state": {key: round(float(value), 6)
                          for key, value in raw.items()},
                "felt": {key: round(float(value), 6)
                         for key, value in felt.items()},
                "dominant": dominant,
            }

        relationships = {}
        for source_id in self.actors:
            for target_id in self.actors:
                if source_id == target_id:
                    continue
                rel = self.relationship(source_id, target_id)
                relationships[f"{source_id}->{target_id}"] = rel.to_dict()

        return {
            "world_id": self.world_id,
            "turn": self.turn,
            "actor_order": tuple(self.actors),
            "actors": actors,
            "relationships": relationships,
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "strength": round(edge.strength, 6),
                }
                for edge in self.edges()
            ],
            "proactive": self.proactive.snapshot(),
        }

    def close(self) -> None:
        if self._owns_store:
            self.store.close()


__all__ = [
    "DEFAULT_TENSION",
    "DEFAULT_TRUST",
    "DEFAULT_WORLD_ID",
    "FriendScene",
    "MAX_FAMILIARITY",
    "MAX_LOVE",
    "MAX_TENSION",
    "MAX_TRUST",
    "PeerRelationship",
    "PeerRelationshipStore",
    "SOCIAL_SCHEMA_VERSION",
    "SceneActor",
]
