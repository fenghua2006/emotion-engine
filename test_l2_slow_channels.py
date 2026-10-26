"""L2 慢通道回归测试（unittest，无外部依赖）。

覆盖 trust / love / longing / guilt 的独立动力学、slow → fast 基线口径、
30 天有界性、guilt 0.3 门槛、快照恢复，以及 current / legacy 双 golden。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest

from core import (
    Appraisal,
    Channel,
    EmotionalState,
    FAST_CHANNELS,
    GUILT_SELF_AGENCY_THRESHOLD,
    MAX_LOVE,
    MAX_TRUST,
    Personality,
    apply_interactions,
    appraise,
    grow_slow_channels,
    interaction_baselines,
    trust_target,
)
from engine import (
    EmotionEngine,
    MemoryStore,
    SensitizationStore,
    load_snapshot,
    save_snapshot,
)
from l2_events import EVENTS, event_by_key
from slowline import SlowLine

ROOT = os.path.dirname(os.path.abspath(__file__))
GOLDEN_PATH = os.path.join(ROOT, "fixtures", "l2_golden.json")
LEGACY_GOLDEN_PATH = os.path.join(ROOT, "fixtures", "l2_golden_legacy.json")


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


CURRENT_GOLDEN = load_json(GOLDEN_PATH)
LEGACY_GOLDEN = load_json(LEGACY_GOLDEN_PATH)


def make_engine(*, legacy_slow=False, legacy_interactions=False, **personality):
    pers = Personality(**personality)
    state = EmotionalState()
    for channel, value in pers.baseline().items():
        setattr(state, channel.value, value)
    return EmotionEngine(
        state=state,
        personality=pers,
        memory=MemoryStore(),
        scars=SensitizationStore(),
        legacy_slow_channels=legacy_slow,
        legacy_interactions=legacy_interactions,
    )


def make_line(*, legacy_slow=False, legacy_interactions=False, **personality):
    return SlowLine(
        personality=Personality(**personality),
        legacy_slow=legacy_slow,
        legacy_interactions=legacy_interactions,
    )


def positive_event(**overrides):
    values = dict(
        goal_relevance=0.8,
        goal_conduciveness=0.7,
        expectedness=0.5,
        other_agency=0.8,
        coping_potential=0.5,
        social_evaluation=0.6,
    )
    values.update(overrides)
    return Appraisal(**values)


def negative_event(self_agency=0.2, **overrides):
    values = dict(
        goal_relevance=0.9,
        goal_conduciveness=-0.8,
        expectedness=0.5,
        other_agency=1.0 - self_agency,
        coping_potential=0.3,
        social_evaluation=-0.4,
    )
    values.update(overrides)
    return Appraisal(**values)


class L2GoldenTests(unittest.TestCase):
    def _assert_step(self, step, expected):
        self.assertEqual(step.mode, expected["mode"])
        self.assertEqual(step.dominant, expected["dominant"])
        self.assertEqual(list(step.shock_channels), expected["shock_channels"])
        self.assertEqual(list(step.blends), expected["blends"])
        self.assertEqual(
            step.slow_positive_input, expected["slow_positive_input"],
        )
        self.assertAlmostEqual(
            step.trust_target, expected["trust_target"], places=9,
        )
        for name, value in expected["values"].items():
            self.assertTrue(
                math.isclose(step.values[name], value, abs_tol=1e-9),
                f"{step.key}.{name}: {step.values[name]} != {value}",
            )
        for name, value in expected["raw_appraisal"].items():
            self.assertTrue(
                math.isclose(
                    step.raw_appraisal[name], value, abs_tol=1e-9,
                ),
                f"{step.key}.raw.{name}",
            )
        for name, value in expected["gated_appraisal"].items():
            self.assertTrue(
                math.isclose(
                    step.gated_appraisal[name], value, abs_tol=1e-9,
                ),
                f"{step.key}.gated.{name}",
            )

    def test_current_sequence_matches_golden(self):
        line = make_line()
        steps = line.run(EVENTS)
        self.assertEqual(len(steps), len(CURRENT_GOLDEN["sequence"]))
        for step, expected in zip(steps, CURRENT_GOLDEN["sequence"]):
            self.assertEqual(step.key, expected["key"])
            self._assert_step(step, expected)

    def test_current_isolated_matches_golden(self):
        for event in EVENTS:
            line = make_line()
            if event.offline_hours > 0:
                step = line.offline(event.offline_hours, key=event.key)
            else:
                step = line.step(
                    event.appraisal,
                    elapsed_minutes=event.elapsed_minutes,
                    boundary_violation=event.boundary_violation,
                    key=event.key,
                )
            self._assert_step(
                step, CURRENT_GOLDEN["isolated"][event.key],
            )

    def test_legacy_sequence_matches_golden(self):
        line = make_line(legacy_slow=True, legacy_interactions=True)
        steps = line.run(EVENTS)
        for step, expected in zip(steps, LEGACY_GOLDEN["sequence"]):
            self.assertEqual(step.key, expected["key"])
            self._assert_step(step, expected)

    def test_golden_metadata_locks_l2_baseline_choices(self):
        meta = CURRENT_GOLDEN["meta"]
        self.assertEqual(meta["layer"], "L2")
        self.assertEqual(
            meta["guilt_self_agency_threshold"],
            GUILT_SELF_AGENCY_THRESHOLD,
        )
        self.assertEqual(meta["positive_event_input"], "per_tick")
        self.assertEqual(
            meta["trust_interaction_baseline"], "trust_target(love)",
        )
        self.assertEqual(
            meta["love_interaction_baseline"], "personality",
        )
        self.assertEqual(meta["interaction_mode"], "relative_to_baseline")

        legacy = LEGACY_GOLDEN["meta"]
        self.assertEqual(legacy["guilt_self_agency_threshold"], 0.0)
        self.assertEqual(legacy["positive_event_input"], "cumulative_legacy")
        self.assertEqual(legacy["trust_interaction_baseline"], "personality")
        self.assertEqual(legacy["interaction_mode"], "legacy_absolute")


class GuiltGateTests(unittest.TestCase):
    def test_guilt_is_zero_at_or_below_threshold(self):
        for agency in (0.0, 0.1, 0.25, 0.3):
            raw = appraise(negative_event(self_agency=agency))
            self.assertEqual(raw[Channel.GUILT], 0.0, agency)

    def test_guilt_activates_above_threshold_and_is_monotonic(self):
        values = [
            appraise(negative_event(self_agency=agency))[Channel.GUILT]
            for agency in (0.31, 0.5, 0.7, 0.9, 1.0)
        ]
        self.assertGreater(values[0], 0.0)
        self.assertEqual(values, sorted(values))

    def test_guilt_threshold_zero_reproduces_legacy(self):
        raw = appraise(
            negative_event(self_agency=0.25),
            guilt_self_agency_threshold=0.0,
        )
        self.assertGreater(raw[Channel.GUILT], 0.0)

    def test_engine_external_attribution_never_creates_guilt(self):
        engine = make_engine()
        result = engine.tick(
            negative_event(self_agency=0.0),
            now=engine.state._last_update + 60,
        )
        self.assertEqual(result["raw_appraisal"]["guilt"], 0.0)
        self.assertEqual(result["state"]["guilt"], 0.0)

        low = make_engine()
        low_result = low.tick(
            negative_event(self_agency=0.25),
            now=low.state._last_update + 60,
        )
        self.assertEqual(low_result["raw_appraisal"]["guilt"], 0.0)

        high = make_engine()
        high_result = high.tick(
            negative_event(self_agency=0.9),
            now=high.state._last_update + 60,
        )
        self.assertGreater(high_result["raw_appraisal"]["guilt"], 0.0)

    def test_legacy_slow_restores_guilt_below_threshold(self):
        event = negative_event(self_agency=0.25)
        current = make_engine()
        legacy = make_engine(legacy_slow=True)
        now = max(current.state._last_update, legacy.state._last_update)
        current.state._last_update = now
        legacy.state._last_update = now
        current_result = current.tick(event, now=now)
        legacy_result = legacy.tick(event, now=now)
        self.assertEqual(current_result["raw_appraisal"]["guilt"], 0.0)
        self.assertGreater(legacy_result["raw_appraisal"]["guilt"], 0.0)


class SlowChannelDynamicsTests(unittest.TestCase):
    def test_trust_converges_to_target_without_overshoot(self):
        state = EmotionalState(love=0.5, trust=0.1)
        for _ in range(500):
            grow_slow_channels(state, 0, 0, 1.0)
        target = trust_target(state.love)
        self.assertLess(abs(state.trust - target), 1e-3)

    def test_love_erodes_when_trust_gap_is_large(self):
        state = EmotionalState(love=0.5, trust=0.05)
        before = state.love
        grow_slow_channels(state, 0, 0, 24.0)
        self.assertLess(state.love, before)

    def test_love_grows_when_trust_is_healthy(self):
        state = EmotionalState(love=0.3, trust=0.6)
        before = state.love
        grow_slow_channels(state, 0, 1, 1.0)
        self.assertGreater(state.love, before)

    def test_positive_event_input_is_per_interval_not_cumulative(self):
        base = EmotionalState(love=0.3, trust=0.5)
        one = EmotionalState(love=0.3, trust=0.5)
        ten = EmotionalState(love=0.3, trust=0.5)
        grow_slow_channels(base, 0, 0, 0.5)
        grow_slow_channels(one, 0, 1, 0.5)
        grow_slow_channels(ten, 0, 10, 0.5)
        one_gain = one.love - base.love
        ten_gain = ten.love - base.love
        self.assertAlmostEqual(ten_gain, one_gain * 10, places=12)

    def test_slow_channels_are_clamped(self):
        state = EmotionalState(love=MAX_LOVE, trust=MAX_TRUST)
        grow_slow_channels(state, 0, 100, 1000.0)
        self.assertLessEqual(state.love, MAX_LOVE)
        self.assertLessEqual(state.trust, MAX_TRUST)

    def test_interaction_baselines_use_trust_target_for_trust_only(self):
        personality = Personality()
        base = interaction_baselines(personality, love=0.6)
        self.assertAlmostEqual(base[Channel.TRUST], trust_target(0.6))
        self.assertAlmostEqual(
            base[Channel.LOVE], personality.baseline()[Channel.LOVE],
        )
        self.assertAlmostEqual(
            base[Channel.JOY], personality.baseline()[Channel.JOY],
        )

    def test_trust_at_equilibrium_does_not_suppress_fear(self):
        personality = Personality()
        love = 0.25
        base = interaction_baselines(personality, love)
        state = EmotionalState(love=love, trust=trust_target(love), fear=0.5)
        apply_interactions(state, baseline=base,
                           trust_ceiling=trust_target(love))
        self.assertAlmostEqual(state.fear, 0.5)

    def test_trust_above_equilibrium_suppresses_fear(self):
        personality = Personality()
        love = 0.25
        target = trust_target(love)
        base = interaction_baselines(personality, love)
        state = EmotionalState(love=love, trust=target + 0.2, fear=0.5)
        apply_interactions(state, baseline=base, trust_ceiling=target)
        self.assertLess(state.fear, 0.5)

    def test_trust_positive_interactions_are_capped_by_target(self):
        personality = Personality()
        love = 0.6
        target = trust_target(love)
        base = interaction_baselines(personality, love)
        state = EmotionalState(
            love=love, trust=target - 0.01, guilt=1.0,
        )
        apply_interactions(state, baseline=base, trust_ceiling=target)
        self.assertLessEqual(state.trust, target + 1e-12)

    def test_anger_suppresses_trust_by_activation_not_absolute(self):
        personality = Personality()
        baseline_anger = personality.baseline()[Channel.ANGER]
        # love=0 排除 LOVE→TRUST 的额外增量，只观察 anger 的作用。
        base = interaction_baselines(personality, love=0.0)
        relative = EmotionalState(
            love=0.0, trust=0.5, anger=baseline_anger + 0.10,
        )
        legacy = relative.clone()
        apply_interactions(relative, baseline=base, trust_ceiling=0.0)
        apply_interactions(legacy, baseline=None)
        relative_drop = 0.5 - relative.trust
        legacy_drop = 0.5 - legacy.trust
        self.assertAlmostEqual(relative_drop, 0.10 * 0.08, places=12)
        self.assertGreater(legacy_drop, relative_drop)

    def test_guilt_scales_love_and_suppresses_anger(self):
        personality = Personality()
        base = interaction_baselines(personality, love=0.5)
        state = EmotionalState(
            love=0.5, trust=trust_target(0.5), anger=1.0, guilt=0.5,
        )
        apply_interactions(state, baseline=base,
                           trust_ceiling=trust_target(0.5))
        expected_love = 0.5 + 0.5 * 0.05 * (1 - 0.5 / MAX_LOVE)
        self.assertAlmostEqual(state.love, expected_love, places=12)
        self.assertAlmostEqual(state.anger, 1.0 - 0.5 * 0.3, places=12)


class LongingTests(unittest.TestCase):
    def test_offline_grows_longing_and_online_decays_it(self):
        line = make_line()
        line.state.love = 0.5
        line.state.trust = trust_target(0.5)
        offline = line.offline(2.0)
        self.assertGreater(offline.values["longing"], 0.0)

        online = line.step(elapsed_minutes=60.0)
        self.assertLess(online.values["longing"], offline.values["longing"])
        # 本 tick 的 floor 用生长前的 love 算；love 随后仍会微量生长，
        # 所以允许一个 tick 内的小偏差。
        self.assertGreaterEqual(
            online.values["longing"], line.state.love * 0.05 - 1e-4,
        )

    def test_no_love_no_longing(self):
        line = make_line()
        line.state.love = 0.0
        step = line.offline(24.0)
        self.assertEqual(step.values["longing"], 0.0)

    def test_longing_is_capped_by_love(self):
        line = make_line()
        line.state.love = 0.5
        line.state.trust = trust_target(0.5)
        step = line.offline(10 ** 6)
        self.assertLessEqual(
            step.values["longing"], line.state.love * 2 + 1e-9,
        )

    def test_trust_decays_after_seven_offline_days(self):
        seven = make_line()
        eight = make_line()
        for line in (seven, eight):
            line.state.love = 0.0
            line.state.trust = 0.8
        seven_step = seven.offline(7 * 24)
        eight_step = eight.offline(8 * 24)
        self.assertLess(
            eight_step.values["trust"], seven_step.values["trust"],
        )


class EngineL2IntegrationTests(unittest.TestCase):
    def test_idle_fast_channels_return_to_baseline(self):
        engine = make_engine()
        baseline = engine.personality.baseline()
        now = engine.state._last_update
        for _ in range(300):
            now += 600
            engine.tick(now=now)
        for channel in FAST_CHANNELS:
            self.assertAlmostEqual(
                getattr(engine.state, channel.value),
                baseline[channel],
                places=9,
                msg=channel.value,
            )
        self.assertGreater(engine.state.trust, baseline[Channel.TRUST])
        self.assertGreater(engine.state.love, baseline[Channel.LOVE])

    def test_idle_trust_reaches_its_relationship_target(self):
        engine = make_engine()
        now = engine.state._last_update
        for _ in range(300):
            now += 600
            engine.tick(now=now)
        self.assertLess(
            abs(engine.state.trust - trust_target(engine.state.love)),
            0.01,
        )

    def test_trace_positive_input_is_per_tick(self):
        engine = make_engine()
        now = engine.state._last_update
        event = positive_event()
        first = engine.tick(event, now=now + 600, trace=True)
        second = engine.tick(event, now=now + 1200, trace=True)
        self.assertEqual(first["trace"]["slow_positive_input"], 1)
        self.assertEqual(second["trace"]["slow_positive_input"], 1)
        self.assertGreater(first["trace"]["trust_target"], 0.0)

    def test_betrayal_halves_trust_and_erodes_love(self):
        line = make_line()
        line.state.trust = 0.8
        line.state.love = 0.6
        step = line.step(
            negative_event(self_agency=0.0, goal_conduciveness=-0.9),
            elapsed_minutes=0.0,
        )
        self.assertLess(step.values["trust"], 0.5)
        self.assertLess(step.values["love"], 0.55)

    def test_thirty_day_simulation_stays_bounded(self):
        engine = make_engine()
        now = engine.state._last_update
        event = positive_event()
        for i in range(30 * 24 * 6):
            now += 600
            engine.tick(event if i % 12 == 0 else None, now=now)
        for channel in Channel:
            value = getattr(engine.state, channel.value)
            self.assertTrue(math.isfinite(value), channel.value)
        self.assertLessEqual(engine.state.love, MAX_LOVE + 1e-12)
        self.assertLessEqual(engine.state.trust, MAX_TRUST + 1e-12)
        self.assertLessEqual(
            engine.state.trust,
            trust_target(engine.state.love) + 0.02,
        )

    def test_legacy_slow_uses_cumulative_positive_events(self):
        events = [
            event_by_key("l2.praised_by_trusted"),
            event_by_key("l2.shared_quiet_time"),
            event_by_key("l2.praised_by_trusted"),
        ]
        current = make_line()
        legacy = make_line(legacy_slow=True, legacy_interactions=True)
        current_steps = current.run(events)
        legacy_steps = legacy.run(events)
        self.assertEqual(
            [step.slow_positive_input for step in current_steps], [1, 1, 1],
        )
        self.assertEqual(
            [step.slow_positive_input for step in legacy_steps], [0, 1, 2],
        )
        self.assertGreater(
            legacy_steps[-1].values["love"],
            current_steps[-1].values["love"],
        )


class L2SnapshotTests(unittest.TestCase):
    def _path(self, directory):
        return os.path.join(directory, "l2_snapshot.db")

    def test_snapshot_preserves_slow_state_and_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            engine = make_engine(legacy_slow=True, legacy_interactions=True)
            engine.state.trust = 0.72
            engine.state.love = 0.83
            engine.state.longing = 0.44
            engine.state.guilt = 0.31
            engine.atmosphere = 0.25
            engine.state._last_update = 123456.0
            self.assertTrue(save_snapshot(engine, path, now=123456.0))

            restored = make_engine()
            self.assertTrue(load_snapshot(restored, path))
            self.assertAlmostEqual(restored.state.trust, 0.72)
            self.assertAlmostEqual(restored.state.love, 0.83)
            self.assertAlmostEqual(restored.state.longing, 0.44)
            self.assertAlmostEqual(restored.state.guilt, 0.31)
            self.assertTrue(restored.legacy_slow_channels)
            self.assertTrue(restored.legacy_interactions)
            self.assertAlmostEqual(restored.atmosphere, 0.25)
            self.assertEqual(restored.state._last_update, 123456.0)

    def test_snapshot_restore_continues_same_slow_curve(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            now = 2_000_000_000.0
            original = make_engine()
            original.state.trust = 0.4
            original.state.love = 0.6
            original.state.longing = 0.2
            original.state.guilt = 0.1
            original.state._last_update = now
            self.assertTrue(save_snapshot(original, path, now=now))

            restored = make_engine()
            self.assertTrue(load_snapshot(restored, path))

            events = [
                event_by_key("l2.cold_shoulder"),
                event_by_key("l2.self_blame"),
                event_by_key("l2.offline_2h"),
                event_by_key("l2.praised_by_trusted"),
            ]
            stamp = now
            for event in events:
                if event.offline_hours > 0:
                    stamp += event.offline_hours * 3600
                    original.wake(now=stamp)
                    restored.wake(now=stamp)
                else:
                    stamp += event.elapsed_minutes * 60
                    original.tick(
                        event.appraisal,
                        boundary_violation=event.boundary_violation,
                        now=stamp,
                    )
                    restored.tick(
                        event.appraisal,
                        boundary_violation=event.boundary_violation,
                        now=stamp,
                    )
            self.assertEqual(original.state.to_dict(), restored.state.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
