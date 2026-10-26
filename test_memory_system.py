"""Relationship memory system tests (unittest, no external deps)."""

import os
import tempfile
import time
import unittest

from memory import (
    MemoryCard,
    MemoryExtractor,
    MemoryService,
    RelationshipMemoryStore,
)


class MemorySystemTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "memory_test.db")
        self.store = RelationshipMemoryStore(self.db_path)
        self.service = MemoryService(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    # ── isolation ──────────────────────────────────────

    def test_user_isolation(self):
        self.store.add_card(MemoryCard(
            summary="Alice 喜欢猫，养了一只橘猫。",
            character_id="furina", user_id="alice",
            tags=["pet", "cat"],
        ))
        self.store.upsert_fact("furina", "alice", "identity.name", "小枫")

        self.assertEqual(
            self.store.recall_cards("猫", "furina", "bob"), []
        )
        self.assertEqual(
            self.store.get_facts("furina", "bob"), []
        )
        self.assertEqual(
            len(self.store.recall_cards("猫", "furina", "alice")), 1
        )

    # ── facts ──────────────────────────────────────────

    def test_fact_supersession(self):
        old_id = self.store.upsert_fact(
            "furina", "alice", "job", "设计师",
        )
        new_id = self.store.upsert_fact(
            "furina", "alice", "job", "产品经理",
        )

        active = self.store.get_facts("furina", "alice")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].value, "产品经理")
        self.assertEqual(active[0].id, new_id)

        old = self.store.db.execute(
            "SELECT status FROM user_facts WHERE id=?", (old_id,)
        ).fetchone()
        self.assertEqual(old["status"], "superseded")

    def test_same_fact_confirmation_does_not_duplicate(self):
        first = self.store.upsert_fact(
            "furina", "alice", "identity.name", "小枫",
        )
        second = self.store.upsert_fact(
            "furina", "alice", "identity.name", "小枫",
        )
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.get_facts("furina", "alice")), 1)

    # ── recall ─────────────────────────────────────────

    def test_query_relevance(self):
        self.store.add_card(MemoryCard(
            summary="她被老板骂了，很沮丧。",
            character_id="furina", user_id="alice",
            tags=["工作", "老板"],
            salience=0.6,
        ))
        self.store.add_card(MemoryCard(
            summary="她喜欢橘猫，家里养了一只。",
            character_id="furina", user_id="alice",
            tags=["宠物", "猫"],
            salience=0.6,
        ))

        cards = self.store.recall_cards(
            "今天又被老板骂了", "furina", "alice", limit=2,
        )
        self.assertTrue(cards)
        self.assertIn("老板", cards[0].summary)

    def test_high_salience_card_surfaces_without_query_match(self):
        self.store.add_card(MemoryCard(
            summary="她说过，被否定的时候最需要的是陪伴，不是建议。",
            character_id="furina", user_id="alice",
            kind="boundary",
            salience=1.0,
            confidence=0.95,
        ))
        cards = self.store.recall_cards(
            "今天天气不错", "furina", "alice", limit=1,
        )
        self.assertEqual(len(cards), 1)
        self.assertIn("陪伴", cards[0].summary)

    # ── open loops ─────────────────────────────────────

    def test_open_loop_lifecycle(self):
        loop_id = self.store.add_open_loop(
            "周五项目汇报", character_id="furina", user_id="alice",
            importance=0.9,
        )
        loops = self.store.recall_open_loops(
            "周五要汇报了", "furina", "alice", limit=2,
        )
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0].id, loop_id)

        self.store.close_open_loop(loop_id)
        self.assertEqual(
            self.store.get_open_loops("furina", "alice"), []
        )
        self.assertEqual(
            self.store.recall_open_loops("周五要汇报了", "furina", "alice"),
            [],
        )

    # ── prompt rendering ───────────────────────────────

    def test_render_respects_budget(self):
        for i in range(20):
            self.store.add_card(MemoryCard(
                summary=f"第{i}件往事：" + "这是一段很长的记忆内容，" * 5,
                character_id="furina", user_id="alice",
                salience=0.5,
            ))
        result = self.store.recall(
            "往事", "furina", "alice", limit_cards=20,
        )
        rendered = self.store.render_context(result, budget_chars=400)
        self.assertLessEqual(len(rendered), 400)
        self.assertIn("【你记得的】", rendered)
        self.assertIn("不要逐条复述", rendered)

    def test_render_avoids_numeric_emotion_dump(self):
        self.service.remember_event(
            "furina", "alice",
            "她第一次主动分享自己的画。",
            kind="milestone",
            emotion_snap={"joy": 0.8, "trust": 0.7},
        )
        context = self.service.build_context(
            "furina", "alice", "画画", budget_chars=1200,
        )
        self.assertIn("第一次主动分享", context)
        self.assertNotIn("joy=", context)
        self.assertNotIn("trust=", context)

    # ── relationship ───────────────────────────────────

    def test_relationship_update(self):
        relationship = self.store.update_relationship(
            "furina", "alice",
            trust=0.8,
            love=0.7,
            familiarity=0.5,
            boundaries=["不要人身攻击"],
            summary="认识三个月，会互相开玩笑。",
        )
        self.assertAlmostEqual(relationship.trust, 0.8)
        self.assertAlmostEqual(relationship.love, 0.7)
        self.assertIn("不要人身攻击", relationship.boundaries)
        self.assertIn("三个月", relationship.summary)

        reloaded = self.store.get_relationship("furina", "alice")
        self.assertAlmostEqual(reloaded.trust, 0.8)

    # ── forget ─────────────────────────────────────────

    def test_forget_user(self):
        self.store.add_card(MemoryCard(
            summary="Alice 的秘密。",
            character_id="furina", user_id="alice",
        ))
        self.store.upsert_fact("furina", "alice", "secret", "yes")
        self.store.add_open_loop("还她一本书", "furina", "alice")
        self.store.update_relationship("furina", "alice", trust=0.9)

        self.store.forget_user("furina", "alice")
        stats = self.store.stats("furina", "alice")
        self.assertEqual(stats, {
            "cards": 0,
            "facts": 0,
            "open_loops": 0,
            "relationships": 0,
        })
        self.assertEqual(
            self.store.recall_cards("秘密", "furina", "alice"), []
        )

    # ── extractor / service ────────────────────────────

    def test_extractor_high_precision_patterns(self):
        candidates = MemoryExtractor().extract(
            "我叫小枫，我不喜欢被说教，我答应周五把方案发给你。"
        )
        keys = {c.fact_key for c in candidates if c.fact_key}
        self.assertIn("identity.name", keys)
        self.assertTrue(any(k.startswith("preference.dislike.") for k in keys))
        self.assertTrue(any(c.kind == "promise" for c in candidates))

        dislike = next(
            c for c in candidates
            if c.fact_key and c.fact_key.startswith("preference.dislike.")
        )
        self.assertEqual(dislike.fact_value, "不喜欢被说教")
        self.assertNotIn("答应", dislike.fact_value)

    def test_explicit_remember_becomes_card(self):
        candidates = MemoryExtractor().extract(
            "记住：她怕黑，晚上不要突然关灯。"
        )
        self.assertTrue(any(c.kind == "note" for c in candidates))

    def test_boundary_pattern_is_not_mixed_with_preference(self):
        candidates = MemoryExtractor().extract("我受不了被说教。")
        boundaries = [c for c in candidates if c.kind == "boundary"]
        self.assertEqual(len(boundaries), 1)
        self.assertIn("被说教", boundaries[0].fact_value)
        self.assertFalse(any(
            c.kind == "preference" for c in candidates
        ))

    def test_remember_turn_writes_fact_and_open_loop(self):
        stored = self.service.remember_turn(
            "furina", "alice",
            "我叫小枫，我不喜欢被说教，我答应周五把方案发给你。",
            raw_ref="turn_001",
        )
        types = [item["type"] for item in stored]
        self.assertIn("fact", types)
        self.assertIn("open_loop", types)

        facts = self.store.get_facts("furina", "alice")
        values = {fact.value for fact in facts}
        self.assertIn("小枫", values)
        self.assertTrue(any("不喜欢" in value for value in values))

        loops = self.store.get_open_loops("furina", "alice")
        self.assertEqual(len(loops), 1)
        self.assertIn("周五", loops[0].summary)

    # ── bias ───────────────────────────────────────────

    def test_memory_bias_is_signed_and_capped(self):
        self.store.add_card(MemoryCard(
            summary="她经历了一次很害怕的冲突。",
            character_id="furina", user_id="alice",
            emotion_snap={"fear": 0.8, "trust": 0.3},
            salience=0.9,
            confidence=0.9,
        ))
        bias = self.store.memory_bias(
            "furina", "alice", baseline={},
        )
        self.assertGreater(bias["fear"], 0.0)
        self.assertLessEqual(abs(bias["fear"]), 0.2)
        self.assertLessEqual(abs(bias["trust"]), 0.2)
        self.assertEqual(bias["joy"], 0.0)

    def test_memory_bias_respects_baseline(self):
        self.store.add_card(MemoryCard(
            summary="平静的一天。",
            character_id="furina", user_id="alice",
            emotion_snap={"fear": 0.2},
            salience=1.0,
            confidence=1.0,
        ))
        bias = self.store.memory_bias(
            "furina", "alice", baseline={"fear": 0.2},
        )
        self.assertAlmostEqual(bias["fear"], 0.0, places=6)

    # ── persistence ────────────────────────────────────

    def test_persistence_across_reopen(self):
        self.store.add_card(MemoryCard(
            summary="她说过喜欢雨天。",
            character_id="furina", user_id="alice",
        ))
        self.store.close()

        reopened = RelationshipMemoryStore(self.db_path)
        try:
            cards = reopened.recall_cards("雨天", "furina", "alice")
            self.assertEqual(len(cards), 1)
            self.assertIn("雨天", cards[0].summary)
        finally:
            reopened.close()

        # tearDown 会再次 close；SQLite close 幂等，保持无害。
        self.store = RelationshipMemoryStore(self.db_path)


class EngineIntegrationTest(unittest.TestCase):
    """回归测试：旧 MemoryStoreDB 的 _db_id 修复 + prompt 记忆注入。"""

    def test_memory_store_sets_db_id_and_consolidates(self):
        from engine import MemoryItem, MemoryStoreDB, init_db

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "engine_memory.db")
            seed = init_db(db_path)
            seed.close()

            store = MemoryStoreDB(db_path)
            try:
                item = MemoryItem(
                    content="她第一次主动分享自己的画。",
                    timestamp=time.time() - 3600,
                    emotion_snap={"joy": 0.8, "trust": 0.7},
                    arousal=0.9,
                    relevance=0.9,
                )
                store.store(item)
                self.assertIsNotNone(getattr(item, "_db_id", None))

                store.consolidate(now=time.time())
                row = store.db.execute(
                    "SELECT tier FROM memories WHERE id=?", (item._db_id,)
                ).fetchone()
                self.assertIn(row[0], ("flash", "long_term"))
            finally:
                store.db.close()

    def test_build_llm_prompt_accepts_relationship_context(self):
        from engine import (
            EmotionEngine,
            EmotionalState,
            MemoryStore,
            Personality,
            SensitizationStore,
            _build_llm_prompt,
        )

        engine = EmotionEngine(
            state=EmotionalState(),
            personality=Personality(),
            memory=MemoryStore(),
            scars=SensitizationStore(),
        )
        context = "【你记得的】\n- 关于对方：\n  - 名字：小枫\n  - 不喜欢被说教"
        prompt = _build_llm_prompt(
            engine, "老板又催我了", "Furina", "测试角色", [],
            memory_context=context,
        )
        self.assertIn("名字：小枫", prompt)
        self.assertIn("不喜欢被说教", prompt)
        self.assertIn("老板又催我了", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
