"""L8 v0 社交场景与角色间关系测试。

覆盖：关系持久化与方向性、关系更新、感染偏置、相关性门控、
场景隔离、快照结构，以及不写 Personality 的边界。
"""

import os
import tempfile
import unittest
from dataclasses import asdict

from engine import (
    Appraisal,
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SensitizationStore,
)
from social import (
    FriendScene,
    PeerRelationship,
    PeerRelationshipStore,
    SceneActor,
)


POSITIVE = Appraisal(
    goal_relevance=0.8,
    goal_conduciveness=0.6,
    expectedness=0.3,
    coping_potential=0.6,
    other_agency=0.4,
    social_evaluation=0.7,
)
NEGATIVE = Appraisal(
    goal_relevance=0.8,
    goal_conduciveness=-0.6,
    expectedness=0.2,
    coping_potential=0.2,
    other_agency=0.6,
    social_evaluation=-0.4,
)
NEUTRAL = Appraisal(
    goal_relevance=0.4,
    goal_conduciveness=0.0,
    expectedness=0.5,
    coping_potential=0.5,
    other_agency=0.2,
    social_evaluation=0.0,
)


def make_engine(extraversion=0.5, neuroticism=0.5):
    personality = Personality(
        openness=0.5,
        conscientiousness=0.5,
        extraversion=extraversion,
        agreeableness=0.5,
        neuroticism=neuroticism,
    )
    return EmotionEngine(
        state=EmotionalState(),
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


def make_actors():
    scene = [
        SceneActor("A", "Alice", make_engine(0.6), "default"),
        SceneActor("B", "Bob", make_engine(0.4), "default"),
        SceneActor("C", "Cara", make_engine(0.5), "default"),
    ]
    return scene


class PeerRelationshipStoreTest(unittest.TestCase):

    def test_round_trip_and_world_isolation(self):
        store = PeerRelationshipStore()
        rel = PeerRelationship(
            world_id="friends",
            source_id="A",
            target_id="B",
            trust=0.7,
            love=0.9,
            familiarity=0.5,
            tension=0.2,
            boundaries=["不深夜打扰", "不深夜打扰", "不翻旧账"],
            summary="测试关系",
            interactions=3,
        )
        store.upsert(rel)

        loaded = store.get("friends", "A", "B")
        self.assertAlmostEqual(loaded.trust, 0.7)
        self.assertAlmostEqual(loaded.love, 0.9)
        self.assertEqual(
            loaded.boundaries, ("不深夜打扰", "不翻旧账"),
        )
        self.assertEqual(loaded.interactions, 3)

        other_world = store.get("other", "A", "B")
        self.assertAlmostEqual(other_world.trust, 0.2)
        self.assertEqual(other_world.boundaries, ())
        self.assertEqual(store.all("friends")[("A", "B")].summary, "测试关系")
        store.close()

    def test_values_are_clamped_and_self_loop_rejected(self):
        rel = PeerRelationship(
            source_id="A", target_id="B",
            trust=5.0, love=-1.0, familiarity=9.0, tension=3.0,
        )
        self.assertEqual(rel.trust, 1.0)
        self.assertEqual(rel.love, 0.0)
        self.assertEqual(rel.familiarity, 1.0)
        self.assertEqual(rel.tension, 1.0)

        with self.assertRaises(ValueError):
            PeerRelationship(source_id="A", target_id="A")


class FriendSceneTest(unittest.TestCase):

    def test_rejects_shared_engines_or_resources(self):
        engine = make_engine()
        actors = [
            SceneActor("A", "Alice", engine),
            SceneActor("B", "Bob", engine),
        ]
        with self.assertRaises(ValueError):
            FriendScene(actors)

        first = make_engine()
        second = make_engine()
        second.memory = first.memory
        with self.assertRaises(ValueError):
            FriendScene([
                SceneActor("A", "Alice", first),
                SceneActor("B", "Bob", second),
            ])

    def test_edges_are_directional_and_tension_weakens(self):
        scene = FriendScene(make_actors(), tension_penalty=0.5)
        scene.set_relationship("A", "B", trust=0.9, love=1.6,
                               familiarity=0.8, tension=0.0)
        scene.set_relationship("B", "A", trust=0.1, love=0.1,
                               familiarity=0.1, tension=0.8)
        edges = {(edge.source, edge.target): edge.strength
                 for edge in scene.edges()}
        self.assertGreater(edges[("A", "B")], edges[("B", "A")])
        scene.close()

    def test_bias_comes_only_from_the_source(self):
        scene = FriendScene(make_actors())
        scene.set_relationship("A", "B", trust=0.9, love=1.5,
                               familiarity=0.8)
        biases = scene.biases_for("A", NEGATIVE)
        self.assertIn("B", biases)
        self.assertNotIn("A", biases)
        for value in biases["B"].values():
            self.assertLessEqual(abs(value), scene.cap + 1e-9)
        self.assertEqual(scene.biases_for("A", None), {})
        self.assertEqual(scene.biases_for("missing", NEGATIVE), {})
        scene.close()

    def test_relevance_gate_can_suppress_a_target(self):
        scene = FriendScene(make_actors())
        scene.set_relationship("A", "B", trust=0.9, love=1.5,
                               familiarity=0.8)
        gated = scene.biases_for(
            "A", NEGATIVE, relevance={"B": 0.0, "C": 1.0},
        )
        self.assertNotIn("B", gated)
        self.assertIn("C", gated)
        scene.close()

    def test_positive_turn_increases_trust_and_familiarity(self):
        scene = FriendScene(make_actors())
        before = scene.relationship("B", "A")
        scene.record_turn("A", {"B": POSITIVE, "C": NEUTRAL})
        after = scene.relationship("B", "A")
        self.assertGreater(after.trust, before.trust)
        self.assertGreater(after.familiarity, before.familiarity)
        self.assertAlmostEqual(after.tension, 0.0)
        self.assertEqual(after.interactions, 1)
        self.assertTrue(after.summary)
        scene.close()

    def test_negative_turn_increases_tension_and_lowers_trust(self):
        scene = FriendScene(make_actors())
        before = scene.relationship("B", "A")
        scene.record_turn("A", {"B": NEGATIVE, "C": NEUTRAL})
        after = scene.relationship("B", "A")
        self.assertLess(after.trust, before.trust)
        self.assertGreater(after.tension, 0.0)
        self.assertEqual(after.interactions, 1)
        scene.close()

    def test_boundary_violation_adds_extra_penalty(self):
        scene = FriendScene(make_actors())
        scene.record_turn("A", {"B": NEUTRAL, "C": NEUTRAL})
        baseline = scene.relationship("B", "A")
        scene2 = FriendScene(make_actors())
        scene2.record_turn(
            "A", {"B": NEUTRAL, "C": NEUTRAL},
            boundary_violations={"B": True},
        )
        violated = scene2.relationship("B", "A")
        self.assertLess(violated.trust, baseline.trust)
        self.assertGreater(violated.tension, baseline.tension)
        scene.close()
        scene2.close()

    def test_relationship_updates_are_directional(self):
        scene = FriendScene(make_actors())
        scene.record_turn("A", {"B": POSITIVE, "C": NEGATIVE})
        b_to_a = scene.relationship("B", "A")
        c_to_a = scene.relationship("C", "A")
        self.assertGreater(b_to_a.trust, 0.2)
        self.assertLess(c_to_a.trust, 0.2)
        self.assertGreater(c_to_a.tension, 0.0)
        scene.close()

    def test_persistence_across_store_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "social.db")
            store = PeerRelationshipStore(path)
            scene = FriendScene(make_actors(), world_id="friends",
                                store=store)
            scene.set_relationship("A", "B", trust=0.8, love=1.2,
                                   familiarity=0.7)
            scene.record_turn("A", {"B": POSITIVE, "C": NEUTRAL})
            expected = scene.relationship("B", "A").to_dict()
            store.close()

            reopened = PeerRelationshipStore(path)
            scene2 = FriendScene(make_actors(), world_id="friends",
                                 store=reopened)
            loaded = scene2.relationship("B", "A").to_dict()
            self.assertAlmostEqual(loaded["trust"], expected["trust"])
            self.assertAlmostEqual(loaded["love"], expected["love"])
            self.assertAlmostEqual(loaded["familiarity"],
                                   expected["familiarity"])
            self.assertEqual(loaded["interactions"],
                             expected["interactions"])
            reopened.close()

    def test_step_ticks_all_actors_and_keeps_states_isolated(self):
        scene = FriendScene(make_actors())
        initial = {
            actor_id: actor.engine.state.to_dict()
            for actor_id, actor in scene.actors.items()
        }
        result = scene.step(
            "A", {"A": POSITIVE, "B": NEUTRAL, "C": NEUTRAL},
        )
        self.assertEqual(result["turn"], 1)
        self.assertIn("B", result["biases"])
        self.assertIn("A", result["actors"])
        self.assertIn("B", result["actors"])
        self.assertIn("C", result["actors"])
        self.assertNotEqual(
            scene.actors["A"].engine.state.to_dict(), initial["A"],
        )
        self.assertIsNot(
            scene.actors["A"].engine.state,
            scene.actors["B"].engine.state,
        )
        self.assertIsNot(
            scene.actors["A"].engine.memory,
            scene.actors["B"].engine.memory,
        )
        scene.close()

    def test_snapshot_contains_relationships_and_edges(self):
        scene = FriendScene(make_actors())
        scene.set_relationship("A", "B", trust=0.7)
        payload = scene.snapshot()
        self.assertEqual(payload["world_id"], "default")
        self.assertEqual(payload["turn"], 0)
        self.assertEqual(
            set(payload["actor_order"]), {"A", "B", "C"},
        )
        self.assertIn("A->B", payload["relationships"])
        self.assertIn("B->A", payload["relationships"])
        self.assertTrue(payload["edges"])
        scene.close()

    def test_user_turn_does_not_update_peer_relationships(self):
        scene = FriendScene(make_actors())
        before = scene.relationship("B", "A").to_dict()
        turn = scene.record_turn("user", {"A": POSITIVE, "B": NEGATIVE})
        after = scene.relationship("B", "A").to_dict()
        self.assertEqual(turn["turn"], 1)
        self.assertEqual(turn["relationship_changes"], {})
        self.assertEqual(after["trust"], before["trust"])
        self.assertEqual(after["tension"], before["tension"])
        scene.close()

    def test_personality_is_never_written(self):
        scene = FriendScene(make_actors())
        before = {
            actor_id: asdict(actor.engine.personality)
            for actor_id, actor in scene.actors.items()
        }
        scene.step("A", {"A": POSITIVE, "B": NEGATIVE, "C": NEUTRAL})
        for actor_id, actor in scene.actors.items():
            self.assertEqual(asdict(actor.engine.personality),
                             before[actor_id])
        scene.close()


if __name__ == "__main__":
    unittest.main()
