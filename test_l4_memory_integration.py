"""L4 关系域记忆接入测试（unittest，无外部依赖）。"""

import dataclasses
import json
import os
import sqlite3
import tempfile
import unittest

from core import Appraisal, apply_appraisal_bias
from engine import (
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SensitizationStore,
    respond,
    respond_llm,
)
from memory import (
    MemoryCandidate,
    MemoryCard,
    MemoryService,
    MemoryWriteGate,
    RelationshipMemoryStore,
    migrate_legacy_memories,
)


NOW = 1_700_000_000.0


def make_engine() -> EmotionEngine:
    personality = Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    state._last_update = NOW
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


def negative_appraisal() -> Appraisal:
    return Appraisal(
        goal_relevance=0.8,
        goal_conduciveness=-0.5,
        expectedness=0.5,
        coping_potential=0.5,
        other_agency=0.5,
        social_evaluation=-0.2,
    )


class AppraisalBiasTest(unittest.TestCase):
    def test_bias_is_capped_per_dimension(self):
        base = Appraisal()
        biased = apply_appraisal_bias(base, {
            "goal_relevance": 1.0,
            "goal_conduciveness": 1.0,
            "expectedness": 1.0,
            "coping_potential": 1.0,
            "other_agency": 1.0,
            "social_evaluation": 1.0,
        }, cap=0.2)
        self.assertAlmostEqual(biased.goal_relevance, 0.2)
        self.assertAlmostEqual(biased.goal_conduciveness, 0.2)
        self.assertAlmostEqual(biased.expectedness, 0.7)
        self.assertAlmostEqual(biased.coping_potential, 0.7)
        self.assertAlmostEqual(biased.other_agency, 0.2)
        self.assertAlmostEqual(biased.social_evaluation, 0.2)

    def test_bias_respects_appraisal_bounds(self):
        base = Appraisal(
            goal_relevance=0.95,
            goal_conduciveness=-0.95,
            expectedness=0.05,
            coping_potential=0.05,
            other_agency=0.95,
            social_evaluation=-0.95,
        )
        biased = apply_appraisal_bias(base, {
            "goal_relevance": 0.2,
            "goal_conduciveness": -0.2,
            "expectedness": -0.2,
            "coping_potential": -0.2,
            "other_agency": 0.2,
            "social_evaluation": -0.2,
        })
        self.assertEqual(biased.goal_relevance, 1.0)
        self.assertEqual(biased.goal_conduciveness, -1.0)
        self.assertEqual(biased.expectedness, 0.0)
        self.assertEqual(biased.coping_potential, 0.0)
        self.assertEqual(biased.other_agency, 1.0)
        self.assertEqual(biased.social_evaluation, -1.0)

    def test_empty_bias_returns_same_appraisal(self):
        base = negative_appraisal()
        self.assertIs(apply_appraisal_bias(base, None), base)
        self.assertIs(apply_appraisal_bias(base, {}), base)

    def test_store_appraisal_prior_is_signed_and_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipMemoryStore(os.path.join(tmp, "m.db"))
            try:
                store.add_card(MemoryCard(
                    summary="一次很糟的否定。",
                    character_id="furina", user_id="alice",
                    appraisal={
                        "goal_relevance": 0.9,
                        "goal_conduciveness": -0.9,
                        "expectedness": 0.1,
                        "coping_potential": 0.1,
                        "other_agency": 0.9,
                        "social_evaluation": -0.8,
                    },
                    emotion_snap={"sadness": 0.8},
                    salience=1.0,
                    confidence=1.0,
                    created_at=NOW,
                ))
                prior = store.appraisal_prior(
                    "furina", "alice", now=NOW,
                )
                self.assertLess(prior["goal_conduciveness"], 0.0)
                self.assertLess(prior["coping_potential"], 0.0)
                self.assertGreater(prior["other_agency"], 0.0)
                for value in prior.values():
                    self.assertLessEqual(abs(value), 0.2)
            finally:
                store.close()

    def test_card_without_appraisal_yields_zero_prior(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipMemoryStore(os.path.join(tmp, "m.db"))
            try:
                store.add_card(MemoryCard(
                    summary="只存了情绪，没有六维评估。",
                    character_id="furina", user_id="alice",
                    emotion_snap={"fear": 0.7},
                    salience=1.0,
                    confidence=1.0,
                    created_at=NOW,
                ))
                prior = store.appraisal_prior(
                    "furina", "alice", now=NOW,
                )
                self.assertTrue(all(value == 0.0 for value in prior.values()))
            finally:
                store.close()

    def test_appraisal_prior_is_user_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipMemoryStore(os.path.join(tmp, "m.db"))
            try:
                store.add_card(MemoryCard(
                    summary="Alice 的负面事件。",
                    character_id="furina", user_id="alice",
                    appraisal={"goal_conduciveness": -0.9},
                    salience=1.0,
                    confidence=1.0,
                    created_at=NOW,
                ))
                prior = store.appraisal_prior(
                    "furina", "bob", now=NOW,
                )
                self.assertTrue(all(value == 0.0 for value in prior.values()))
            finally:
                store.close()


class EngineMemoryIntegrationTest(unittest.TestCase):
    def test_default_trace_has_no_memory_keys(self):
        engine = make_engine()
        result = engine.tick(negative_appraisal(), now=NOW, trace=True)
        self.assertNotIn("memory_bias", result)
        self.assertNotIn("memory_bias_enabled", result["trace"])
        self.assertNotIn("memory_bias_applied", result["trace"])

    def test_bias_changes_appraisal_only_when_passed(self):
        baseline_engine = make_engine()
        biased_engine = make_engine()
        appraisal = negative_appraisal()
        bias = {
            "goal_conduciveness": -0.2,
            "expectedness": -0.2,
            "coping_potential": -0.2,
        }
        personality_before = dataclasses.asdict(biased_engine.personality)

        plain = baseline_engine.tick(appraisal, now=NOW, trace=True)
        biased = biased_engine.tick(
            appraisal, now=NOW, trace=True, appraisal_bias=bias,
        )

        self.assertGreater(biased["state"]["sadness"], plain["state"]["sadness"])
        self.assertIn("memory_bias", biased)
        self.assertTrue(biased["trace"]["memory_bias_enabled"])
        self.assertLess(
            biased["trace"]["memory_bias_applied"]["goal_conduciveness"], 0.0,
        )
        self.assertAlmostEqual(
            biased["trace"]["appraisal_before_memory"]["goal_conduciveness"],
            -0.5,
        )
        self.assertEqual(
            personality_before,
            dataclasses.asdict(biased_engine.personality),
        )

    def test_bias_without_appraisal_does_not_touch_state(self):
        plain_engine = make_engine()
        biased_engine = make_engine()
        plain = plain_engine.tick(None, now=NOW, trace=True)
        biased = biased_engine.tick(
            None, now=NOW, trace=True,
            appraisal_bias={"goal_conduciveness": -0.2},
        )
        self.assertEqual(plain["state"], biased["state"])
        self.assertNotIn("memory_bias", biased)
        self.assertNotIn("memory_bias_enabled", biased["trace"])

    def test_trace_bias_never_exceeds_cap(self):
        engine = make_engine()
        result = engine.tick(
            negative_appraisal(), now=NOW, trace=True,
            appraisal_bias={
                "goal_relevance": 5.0,
                "goal_conduciveness": -5.0,
                "expectedness": 5.0,
                "coping_potential": -5.0,
                "other_agency": 5.0,
                "social_evaluation": -5.0,
            },
        )
        for value in result["trace"]["memory_bias_applied"].values():
            self.assertLessEqual(abs(value), 0.2 + 1e-9)

    def test_respond_passes_memory_bias(self):
        engine = make_engine()
        result = respond(
            engine, "event", appraisal=negative_appraisal(),
            memory_appraisal_bias={"goal_conduciveness": -0.2},
        )
        self.assertIn("memory_bias", result)
        self.assertLess(result["memory_bias"]["goal_conduciveness"], 0.0)

    def test_respond_llm_auto_recalls_prior(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipMemoryStore(os.path.join(tmp, "m.db"))
            try:
                service = MemoryService(store, auto_summary=False)
                service.remember_event(
                    "furina", "alice", "上次被否定时很难受。",
                    appraisal={
                        "goal_conduciveness": -0.9,
                        "social_evaluation": -0.8,
                    },
                    emotion_snap={"sadness": 0.8},
                    created_at=NOW,
                    now=NOW,
                )
                engine = make_engine()
                result = respond_llm(
                    engine, "今天又被否定了。",
                    character_id="furina", user_id="alice",
                    relationship_memory=service,
                    appraisal=negative_appraisal(),
                    api_key="", record_turn=False,
                )
                self.assertIn("memory_bias", result)
                self.assertLess(
                    result["memory_bias"]["goal_conduciveness"], 0.0,
                )
            finally:
                store.close()

    def test_memory_prior_does_not_write_personality_over_ticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipMemoryStore(os.path.join(tmp, "m.db"))
            try:
                store.add_card(MemoryCard(
                    summary="长期负面记忆。",
                    character_id="furina", user_id="alice",
                    appraisal={"goal_conduciveness": -0.9},
                    salience=1.0, confidence=1.0, created_at=NOW,
                ))
                prior = store.appraisal_prior(
                    "furina", "alice", now=NOW,
                )
                engine = make_engine()
                original = dataclasses.asdict(engine.personality)
                for index in range(10):
                    engine.tick(
                        negative_appraisal(), now=NOW + index * 60,
                        appraisal_bias=prior,
                    )
                self.assertEqual(original, dataclasses.asdict(engine.personality))
            finally:
                store.close()

    def test_empty_prior_is_exactly_no_prior(self):
        plain_engine = make_engine()
        prior_engine = make_engine()
        empty = {key: 0.0 for key in (
            "goal_relevance", "goal_conduciveness", "expectedness",
            "coping_potential", "other_agency", "social_evaluation",
        )}
        plain = plain_engine.tick(negative_appraisal(), now=NOW)
        with_empty = prior_engine.tick(
            negative_appraisal(), now=NOW, appraisal_bias=empty,
        )
        self.assertEqual(plain["state"], with_empty["state"])


class RelationshipSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RelationshipMemoryStore(
            os.path.join(self.tmp.name, "m.db"),
        )
        self.service = MemoryService(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_summary_auto_updates_after_turn(self):
        self.service.remember_turn(
            "furina", "alice",
            "我叫小枫，我不喜欢被说教，我答应周五把方案发给你。",
            now=NOW,
        )
        summary = self.store.get_relationship("furina", "alice").summary
        self.assertIn("小枫", summary)
        self.assertIn("被说教", summary)
        self.assertIn("周五", summary)
        self.assertLessEqual(len(summary), 160)

    def test_summary_is_deterministic(self):
        self.service.remember_event(
            "furina", "alice", "她喜欢雨天。", now=NOW,
        )
        first = self.store.update_relationship_summary(
            "furina", "alice", now=NOW,
        )
        second = self.store.update_relationship_summary(
            "furina", "alice", now=NOW,
        )
        self.assertEqual(first, second)

    def test_manual_summary_survives_when_no_new_data(self):
        self.store.update_relationship(
            "furina", "alice", summary="手写的关系摘要。",
        )
        summary = self.store.update_relationship_summary(
            "furina", "alice", now=NOW,
        )
        self.assertEqual(summary, "手写的关系摘要。")

    def test_auto_summary_can_be_disabled(self):
        service = MemoryService(self.store, auto_summary=False)
        service.remember_turn(
            "furina", "alice", "我叫小枫。", now=NOW,
        )
        self.assertEqual(
            self.store.get_relationship("furina", "alice").summary, "",
        )


class WriteGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RelationshipMemoryStore(
            os.path.join(self.tmp.name, "m.db"),
        )
        self.gate = MemoryWriteGate()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def decide(self, candidate):
        return self.gate.decide(
            candidate, self.store, "furina", "alice", now=NOW,
        )

    def test_low_confidence_is_rejected(self):
        decision = self.decide(MemoryCandidate(
            kind="episode", summary="不太确定的事。", confidence=0.3,
        ))
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, "low_confidence")

    def test_explicit_note_bypasses_confidence_threshold(self):
        decision = self.decide(MemoryCandidate(
            kind="note", summary="记住这件事。", confidence=0.1,
            tags=["explicit"],
        ))
        self.assertTrue(decision.accept)
        self.assertEqual(decision.reason, "explicit")

    def test_duplicate_card_is_rejected(self):
        self.store.add_card(MemoryCard(
            summary="她喜欢橘猫，家里养了一只。",
            character_id="furina", user_id="alice", created_at=NOW,
        ))
        decision = self.decide(MemoryCandidate(
            kind="episode", summary="她喜欢橘猫，家里养了一只。",
            confidence=0.9,
        ))
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, "duplicate_card")

    def test_fact_conflict_supersedes(self):
        self.store.upsert_fact(
            "furina", "alice", "preference.like.宠物", "喜欢猫",
            category="preference", now=NOW,
        )
        decision = self.decide(MemoryCandidate(
            kind="preference", summary="喜欢狗",
            fact_key="preference.like.宠物", fact_value="喜欢狗",
            category="preference", confidence=0.9,
        ))
        self.assertTrue(decision.accept)
        self.assertEqual(decision.reason, "supersede_existing")

    def test_duplicate_open_loop_is_rejected(self):
        self.store.add_open_loop(
            "周五项目汇报", character_id="furina", user_id="alice",
            importance=0.9, now=NOW,
        )
        decision = self.decide(MemoryCandidate(
            kind="promise", summary="周五项目汇报", confidence=0.9,
        ))
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, "duplicate_open_loop")

    def test_turn_write_budget_and_decisions_are_exposed(self):
        service = MemoryService(self.store, max_writes_per_turn=1)
        stored = service.remember_turn(
            "furina", "alice",
            "我叫小枫，我不喜欢被说教，我答应周五把方案发给你。",
            now=NOW,
        )
        self.assertEqual(len(stored), 1)
        self.assertTrue(any(
            decision["reason"] == "turn_budget"
            for decision in service.last_gate_decisions
        ))


class LegacyMigrationTest(unittest.TestCase):
    def test_migration_is_idempotent_and_skips_automatic_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy_path = os.path.join(tmp, "emotion.db")
            db = sqlite3.connect(legacy_path)
            db.executescript("""
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    tier TEXT NOT NULL DEFAULT 'short_term',
                    arousal REAL NOT NULL DEFAULT 0,
                    relevance REAL NOT NULL DEFAULT 0,
                    emotion_snap TEXT NOT NULL DEFAULT '{}',
                    timestamp REAL NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 0
                );
            """)
            db.execute(
                "INSERT INTO memories (content,tier,arousal,relevance,"
                "emotion_snap,timestamp) VALUES (?,?,?,?,?,?)",
                ("她第一次主动分享自己的画。", "flash", 0.9, 0.9,
                 json.dumps({"joy": 0.8}), NOW),
            )
            db.execute(
                "INSERT INTO memories (content,tier,arousal,relevance,"
                "emotion_snap,timestamp) VALUES (?,?,?,?,?,?)",
                ("gc=-0.5 gr=0.5", "short_term", 0.2, 0.5, "{}", NOW),
            )
            db.commit()
            db.close()

            store = RelationshipMemoryStore(os.path.join(tmp, "m.db"))
            try:
                first = migrate_legacy_memories(
                    legacy_path, store, "furina", "alice", now=NOW,
                )
                self.assertEqual(first["seen"], 2)
                self.assertEqual(first["migrated"], 1)
                self.assertEqual(first["skipped_automatic"], 1)

                second = migrate_legacy_memories(
                    legacy_path, store, "furina", "alice", now=NOW,
                )
                self.assertEqual(second["migrated"], 0)
                self.assertEqual(second["skipped_duplicate"], 1)

                cards = store.recall_cards(
                    "画", "furina", "alice", limit=5, now=NOW,
                )
                self.assertEqual(len(cards), 1)
                self.assertIn("画", cards[0].summary)
                self.assertEqual(cards[0].kind, "milestone")
            finally:
                store.close()


class PromptBudgetAndAblationTest(unittest.TestCase):
    def test_hundred_turns_stay_within_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipMemoryStore(os.path.join(tmp, "m.db"))
            service = MemoryService(store, auto_summary=False)
            try:
                for index in range(100):
                    service.remember_event(
                        "furina", "alice",
                        f"第{index}次长记忆：" + "她讲了很多细节，" * 4,
                        salience=0.7,
                        created_at=NOW - index * 3600,
                        now=NOW - index * 3600,
                    )
                context = service.build_context(
                    "furina", "alice", "今天又被老板骂了",
                    budget_chars=1200,
                )
                self.assertLessEqual(len(context), 1200)
                self.assertGreater(store.stats("furina", "alice")["cards"], 0)
            finally:
                store.close()

    def test_memory_prior_ablation_changes_state_not_personality(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipMemoryStore(os.path.join(tmp, "m.db"))
            try:
                service = MemoryService(store, auto_summary=False)
                service.remember_event(
                    "furina", "alice", "被否定。",
                    appraisal={
                        "goal_conduciveness": -0.9,
                        "coping_potential": 0.1,
                    },
                    emotion_snap={"sadness": 0.9},
                    created_at=NOW, now=NOW,
                )
                prior = service.appraisal_prior(
                    "furina", "alice", now=NOW,
                )
                plain_engine = make_engine()
                memory_engine = make_engine()
                plain = plain_engine.tick(negative_appraisal(), now=NOW)
                with_memory = memory_engine.tick(
                    negative_appraisal(), now=NOW, appraisal_bias=prior,
                )
                self.assertNotEqual(plain["state"], with_memory["state"])
                self.assertEqual(
                    dataclasses.asdict(plain_engine.personality),
                    dataclasses.asdict(memory_engine.personality),
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
