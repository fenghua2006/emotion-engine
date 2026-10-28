"""L8.5 主动联系核心测试。

覆盖：配置 clamp、安静时段、最小静默、冷却、每日上限、未回复上限、
紧迫度组成、候选选择、标记发送不改引擎 / 关系，以及 FriendScene 集成。
"""

import time
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
from proactive import (
    ProactiveConfig,
    ProactiveScheduler,
)
from social import FriendScene, PeerRelationship, SceneActor


def noon(day=1, hour=12, minute=0):
    return time.mktime((2026, 10, day, hour, minute, 0, 0, 0, -1))


def make_engine(**state):
    personality = Personality(
        openness=0.5,
        conscientiousness=0.5,
        extraversion=0.5,
        agreeableness=0.5,
        neuroticism=0.5,
    )
    return EmotionEngine(
        state=EmotionalState(**state),
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


def make_actors():
    return {
        "A": SceneActor("A", "Alice", make_engine(), "default"),
        "B": SceneActor("B", "Bob", make_engine(), "default"),
        "C": SceneActor("C", "Cara", make_engine(), "default"),
    }


def base_config(**overrides):
    values = {
        "min_silence_seconds": 1800.0,
        "silence_window_seconds": 10800.0,
        "cooldown_seconds": 21600.0,
        "max_per_day": 2,
        "max_unanswered": 2,
        "quiet_start_hour": None,
        "quiet_end_hour": None,
        "threshold": 0.35,
    }
    values.update(overrides)
    return ProactiveConfig(**values)


def make_scheduler(actors=None, *, config=None, now=None):
    actors = actors or make_actors()

    def relationship(source_id, target_id):
        return PeerRelationship(
            source_id=source_id, target_id=target_id,
        )

    return actors, ProactiveScheduler(
        actors, relationship, config=config or base_config(), now=now,
    )


def make_urgent(actors):
    state = actors["A"].engine.state
    state.longing = 1.0
    state.love = 1.0
    state.trust = 0.8


class ProactiveConfigTest(unittest.TestCase):

    def test_defaults_and_from_mapping(self):
        config = ProactiveConfig()
        self.assertEqual(config.min_silence_seconds, 1800.0)
        self.assertEqual(config.max_per_day, 2)
        self.assertEqual(config.quiet_start_hour, 23)

        custom = ProactiveConfig.from_mapping({
            "threshold": "0.6",
            "max_per_day": 4,
            "quiet_start_hour": 22,
            "quiet_end_hour": 6,
            "longing_weight": 0.5,
        })
        self.assertEqual(custom.threshold, 0.6)
        self.assertEqual(custom.max_per_day, 4)
        self.assertEqual(custom.quiet_start_hour, 22)
        self.assertEqual(custom.longing_weight, 0.5)
        self.assertEqual(custom.to_dict()["threshold"], 0.6)

    def test_invalid_values_are_clamped(self):
        config = ProactiveConfig(
            min_silence_seconds=-5,
            silence_window_seconds=-1,
            cooldown_seconds="oops",
            max_per_day=99,
            max_unanswered=-2,
            quiet_start_hour=28,
            quiet_end_hour="",
            threshold=4,
            longing_weight=-1,
        )
        self.assertEqual(config.min_silence_seconds, 0.0)
        self.assertEqual(config.silence_window_seconds, 10800.0)
        self.assertEqual(config.cooldown_seconds, 21600.0)
        self.assertEqual(config.max_per_day, 20)
        self.assertEqual(config.max_unanswered, 0)
        self.assertIsNone(config.quiet_start_hour)
        self.assertIsNone(config.quiet_end_hour)
        self.assertEqual(config.threshold, 1.0)
        self.assertEqual(config.longing_weight, 0.0)


class ProactiveSchedulerTest(unittest.TestCase):

    def test_initial_assessment_is_blocked_by_min_silence(self):
        actors, scheduler = make_scheduler(now=noon())
        result = scheduler.assess("A", now=noon())
        self.assertFalse(result.eligible)
        self.assertEqual(result.blocked_reason, "min_silence")
        self.assertEqual(result.elapsed_seconds, 0.0)
        self.assertLess(result.urgency, 0.35)
        self.assertEqual(result.components["absence"], 0.0)
        self.assertEqual(scheduler.eligible(now=noon()), ())

    def test_high_longing_and_absence_make_actor_eligible(self):
        actors, scheduler = make_scheduler(now=noon())
        make_urgent(actors)
        result = scheduler.assess("A", now=noon() + 4 * 3600)
        self.assertTrue(result.eligible)
        self.assertEqual(result.blocked_reason, "")
        self.assertGreaterEqual(result.urgency, 0.35)
        self.assertIn("longing", result.reasons)
        self.assertIn("absence", result.reasons)
        self.assertGreater(result.components["absence"], 0.2)

    def test_zero_threshold_exposes_low_urgency_but_not_silence(self):
        actors, scheduler = make_scheduler(
            config=base_config(threshold=0.0), now=noon(),
        )
        result = scheduler.assess("A", now=noon() + 3600)
        self.assertTrue(result.eligible)
        self.assertLess(result.urgency, 0.35)

    def test_quiet_hours_wrap_midnight(self):
        actors, scheduler = make_scheduler(
            config=base_config(
                quiet_start_hour=23, quiet_end_hour=7,
            ),
            now=noon(day=1, hour=23),
        )
        self.assertEqual(
            scheduler.assess("A", now=noon(day=1, hour=23)).blocked_reason,
            "quiet_hours",
        )
        self.assertEqual(
            scheduler.assess("A", now=noon(day=2, hour=6)).blocked_reason,
            "quiet_hours",
        )
        afternoon = scheduler.assess("A", now=noon(day=2, hour=12))
        self.assertNotEqual(afternoon.blocked_reason, "quiet_hours")

    def test_cooldown_blocks_after_contact(self):
        actors, scheduler = make_scheduler(now=noon())
        make_urgent(actors)
        now = noon() + 4 * 3600
        candidate = scheduler.assess("A", now=now)
        self.assertTrue(candidate.eligible)
        scheduler.mark_contact(candidate, now=now)
        blocked = scheduler.assess("A", now=now + 3600)
        self.assertEqual(blocked.blocked_reason, "cooldown")

    def test_daily_limit_blocks(self):
        actors, scheduler = make_scheduler(
            config=base_config(
                min_silence_seconds=0, cooldown_seconds=0,
                max_per_day=1, threshold=0.0,
            ),
            now=noon(),
        )
        now = noon()
        scheduler.mark_contact("A", now=now)
        self.assertEqual(
            scheduler.assess("A", now=now).blocked_reason, "daily_limit",
        )

    def test_unanswered_limit_clears_on_user_turn(self):
        actors, scheduler = make_scheduler(
            config=base_config(
                min_silence_seconds=0, cooldown_seconds=0,
                max_per_day=5, max_unanswered=2, threshold=0.0,
            ),
            now=noon(),
        )
        now = noon()
        scheduler.mark_contact("A", now=now)
        scheduler.mark_contact("A", now=now)
        self.assertEqual(
            scheduler.assess("A", now=now).blocked_reason,
            "unanswered_limit",
        )
        scheduler.observe_turn("user", now=now + 10)
        self.assertEqual(scheduler.pending["A"], 0)
        self.assertTrue(scheduler.assess("A", now=now + 10).eligible)

    def test_mark_contact_does_not_touch_engine_or_relationship(self):
        actors, scheduler = make_scheduler(now=noon())
        make_urgent(actors)
        before_state = actors["A"].engine.state.to_dict()
        before_personality = asdict(actors["A"].engine.personality)
        rel = PeerRelationship(source_id="A", target_id="B", trust=0.7)
        before_rel = rel.to_dict()

        receipt = scheduler.mark_contact("A", now=noon() + 100)

        self.assertEqual(receipt["actor_id"], "A")
        self.assertEqual(receipt["pending"], 1)
        self.assertEqual(receipt["daily_count"], 1)
        self.assertEqual(scheduler.total_contacts, 1)
        self.assertEqual(actors["A"].engine.state.to_dict(), before_state)
        self.assertEqual(
            asdict(actors["A"].engine.personality), before_personality,
        )
        self.assertEqual(rel.to_dict(), before_rel)

    def test_choose_returns_highest_urgency(self):
        actors, scheduler = make_scheduler(
            config=base_config(threshold=0.0), now=noon(),
        )
        actors["A"].engine.state.longing = 1.0
        actors["B"].engine.state.longing = 0.4
        actors["C"].engine.state.longing = 0.0
        now = noon() + 4 * 3600
        chosen = scheduler.choose(now=now)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.actor_id, "A")
        self.assertEqual(len(scheduler.eligible(now=now)), 3)

    def test_target_actor_uses_familiarity_and_tension(self):
        actors = make_actors()
        relationship = PeerRelationship(
            source_id="A", target_id="B",
            familiarity=0.8, tension=0.4,
        )
        scheduler = ProactiveScheduler(
            actors, lambda source, target: relationship,
            config=base_config(threshold=0.0), now=noon(),
        )
        result = scheduler.assess(
            "A", now=noon() + 3600, target_id="B",
        )
        self.assertAlmostEqual(result.components["familiarity"], 0.08)
        self.assertAlmostEqual(result.components["tension"], -0.1)
        self.assertIn("familiarity", result.reasons)

    def test_unknown_and_self_targets_are_rejected(self):
        actors, scheduler = make_scheduler(now=noon())
        with self.assertRaises(KeyError):
            scheduler.assess("Z", now=noon())
        with self.assertRaises(KeyError):
            scheduler.assess("A", now=noon(), target_id="Z")
        with self.assertRaises(ValueError):
            scheduler.assess("A", now=noon(), target_id="A")
        with self.assertRaises(KeyError):
            scheduler.mark_contact("Z", now=noon())
        with self.assertRaises(ValueError):
            ProactiveScheduler({}, lambda source, target: None, now=noon())

    def test_snapshot_contains_config_assessments_and_counters(self):
        actors, scheduler = make_scheduler(now=noon())
        scheduler.mark_contact("A", now=noon() + 60)
        snapshot = scheduler.snapshot(now=noon() + 60)
        self.assertEqual(
            set(snapshot),
            {
                "config", "now", "last_activity_at", "last_user_at",
                "last_speaker", "pending", "last_contact_at",
                "daily_counts", "total_contacts", "actors",
            },
        )
        self.assertEqual(set(snapshot["actors"]), {"A", "B", "C"})
        self.assertEqual(snapshot["actors"]["A"]["actor_id"], "A")
        self.assertEqual(snapshot["pending"]["A"], 1)
        self.assertEqual(snapshot["total_contacts"], 1)
        self.assertEqual(snapshot["last_speaker"], "A")


class FriendSceneProactiveIntegrationTest(unittest.TestCase):

    def test_record_turn_observes_and_user_reply_clears_pending(self):
        actors = make_actors()
        scene = FriendScene(
            actors.values(),
            proactive_config=base_config(),
            proactive_now=noon(),
        )
        scene.proactive.mark_contact("A", now=noon() + 100)
        self.assertEqual(scene.proactive.pending["A"], 1)

        scene.record_turn("user", now=noon() + 200)
        self.assertEqual(scene.proactive.pending["A"], 0)
        self.assertEqual(scene.proactive.last_user_at, noon() + 200)
        self.assertEqual(scene.proactive.last_speaker, "user")

        scene.record_turn("A", now=noon() + 300)
        self.assertEqual(scene.proactive.last_speaker, "A")
        self.assertEqual(scene.proactive.last_actor_at["A"], noon() + 300)

        snapshot = scene.snapshot()
        self.assertIn("proactive", snapshot)
        self.assertEqual(
            snapshot["proactive"]["config"]["threshold"], 0.35,
        )
        self.assertIn("A", snapshot["proactive"]["actors"])

        scene.set_proactive_config({"threshold": 0.9})
        self.assertEqual(scene.proactive.config.threshold, 0.9)
        scene.close()

    def test_proactive_check_does_not_tick_or_update_relationship(self):
        actors = make_actors()
        scene = FriendScene(
            actors.values(),
            proactive_config=base_config(threshold=0.0),
            proactive_now=noon(),
        )
        make_urgent(actors)
        scene.set_relationship("A", "B", trust=0.2)
        state = actors["A"].engine.state
        before_update = state._last_update
        before_state = state.to_dict()
        before_rel = scene.relationship("A", "B").to_dict()

        candidate = scene.proactive.choose(now=noon() + 4 * 3600)
        self.assertIsNotNone(candidate)
        scene.proactive.mark_contact(candidate, now=noon() + 4 * 3600)

        self.assertEqual(state._last_update, before_update)
        self.assertEqual(state.to_dict(), before_state)
        self.assertEqual(scene.turn, 0)
        self.assertEqual(
            scene.relationship("A", "B").to_dict(), before_rel,
        )
        scene.close()


class ProactiveBoundaryTest(unittest.TestCase):

    def test_appraisal_type_is_not_mutated(self):
        appraisal = Appraisal(goal_relevance=0.8)
        snapshot = appraisal.to_dict()
        actors, scheduler = make_scheduler(now=noon())
        make_urgent(actors)
        scheduler.assess("A", now=noon() + 4 * 3600)
        self.assertEqual(appraisal.to_dict(), snapshot)


if __name__ == "__main__":
    unittest.main()
