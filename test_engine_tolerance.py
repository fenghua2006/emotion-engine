"""Engine ↔ tolerance integration tests (unittest, no external deps)."""

import os
import tempfile
import unittest

from engine import (
    Appraisal,
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SensitizationStore,
    _build_llm_prompt,
    load_snapshot,
    respond,
    save_snapshot,
)
from tolerance import ZONE_HYPER, ZONE_REGULATED


def make_engine(**personality):
    pers = Personality(**personality)
    state = EmotionalState()
    for channel, value in pers.baseline().items():
        setattr(state, channel.value, value)
    return EmotionEngine(
        state=state,
        personality=pers,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


def bad_event():
    return Appraisal(
        goal_relevance=0.9,
        goal_conduciveness=-0.9,
        expectedness=0.2,
        other_agency=0.9,
        coping_potential=0.1,
    )


class EngineToleranceTests(unittest.TestCase):
    def test_tick_returns_tolerance_and_calm_baseline_is_regulated(self):
        engine = make_engine()
        result = engine.tick(now=engine.state._last_update + 60)
        self.assertIn("tolerance", result)
        self.assertEqual(result["tolerance"]["zone"], ZONE_REGULATED)
        self.assertLess(result["tolerance"]["load"], 0.05)

    def test_repeated_negative_events_enter_hyper(self):
        engine = make_engine(neuroticism=0.7)
        now = engine.state._last_update
        zones = []
        for _ in range(3):
            now += 60
            result = engine.tick(bad_event(), boundary_violation=True, now=now)
            zones.append(result["tolerance"]["zone"])
        self.assertEqual(zones[-1], ZONE_HYPER)

    def test_hyper_amplifies_negative_appraisal(self):
        regulated = make_engine()
        hyper = make_engine()
        hyper.tolerance.zone = ZONE_HYPER

        now = max(regulated.state._last_update, hyper.state._last_update)
        regulated.state._last_update = now
        hyper.state._last_update = now
        calm_result = regulated.tick(bad_event(), now=now)
        hyper_result = hyper.tick(bad_event(), now=now)

        calm_anger = calm_result["gated_appraisal"]["anger"]
        hyper_anger = hyper_result["gated_appraisal"]["anger"]
        # 1.5× 负性增益，再叠加两种状态下略有差异的氛围调制。
        self.assertGreater(hyper_anger, calm_anger * 1.4)
        self.assertLess(hyper_anger, calm_anger * 1.6)

    def test_hyper_slows_negative_decay(self):
        regulated = make_engine()
        hyper = make_engine()
        for engine in (regulated, hyper):
            engine.state.anger = 1.5
            engine.state._last_update -= 3600  # 1 hour elapsed
        hyper.tolerance.zone = ZONE_HYPER

        regulated.tick()
        hyper.tick()
        self.assertGreater(hyper.state.anger, regulated.state.anger)

    def test_snapshot_roundtrip_preserves_control_state(self):
        engine = make_engine()
        engine.tolerance.load = 1.1
        engine.tolerance.capacity = 0.8
        engine.tolerance.zone = ZONE_HYPER
        engine.tolerance.overload_ratio = 1.375
        engine.events_today = 5
        engine.positive_events = 2
        engine._recent_shocks = [0.1, 0.2]
        engine.atmosphere = 0.4

        path = os.path.join(tempfile.gettempdir(), "ee_tolerance_snapshot.db")
        if os.path.exists(path):
            os.remove(path)
        try:
            self.assertTrue(save_snapshot(engine, path, now=123456.0))
            restored = make_engine()
            self.assertTrue(load_snapshot(restored, path))
            self.assertEqual(restored.tolerance.zone, ZONE_HYPER)
            self.assertAlmostEqual(restored.tolerance.load, 1.1)
            self.assertAlmostEqual(restored.tolerance.overload_ratio, 1.375)
            self.assertEqual(restored.events_today, 5)
            self.assertEqual(restored.positive_events, 2)
            self.assertEqual(restored._recent_shocks, [0.1, 0.2])
            self.assertAlmostEqual(restored.atmosphere, 0.4)
            self.assertEqual(restored.state._last_update, 123456.0)
            self.assertEqual(restored.state.to_dict(), engine.state.to_dict())
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_injectable_clock_controls_elapsed_time(self):
        engine = make_engine()
        start = engine.state._last_update
        engine.tick(now=start + 120)
        self.assertEqual(engine.state._last_update, start + 120)
        engine.tick(now=start + 180)
        self.assertEqual(engine.state._last_update, start + 180)

    def test_wake_returns_tolerance(self):
        engine = make_engine()
        result = engine.wake(now=engine.state._last_update + 6 * 3600)
        self.assertIn("tolerance", result)
        self.assertGreaterEqual(result["offline_minutes"], 1)

    def test_prompt_and_respond_expose_zone(self):
        engine = make_engine()
        engine.tolerance.zone = ZONE_HYPER
        prompt = _build_llm_prompt(engine, "你还好吗", "Furina", "测试角色", [])
        self.assertIn("超出耐受窗", prompt)
        self.assertIn("战或逃", prompt)

        local = respond(engine, "test")
        self.assertEqual(local["tolerance"]["zone"], ZONE_HYPER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
