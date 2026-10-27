"""Tolerance window tests (unittest, no external deps)."""

import unittest

from tolerance import (
    ZONE_HYPER,
    ZONE_HYPO,
    ZONE_REGULATED,
    ToleranceState,
)


BASE = {
    "joy": 0.30,
    "sadness": 0.10,
    "anger": 0.05,
    "fear": 0.05,
    "love": 0.20,
    "disgust": 0.00,
    "surprise": 0.00,
    "trust": 0.25,
    "longing": 0.00,
    "guilt": 0.00,
}


def make_state(**overrides):
    state = dict(BASE)
    state.update(overrides)
    return state


def update(tolerance, state, **kwargs):
    params = {
        "state": state,
        "baseline": BASE,
        "trust": state["trust"],
        "love": state["love"],
        "trust_target_value": 0.24,
        "neuroticism": 0.5,
        "atmosphere": -0.3,
        "shock_channels": (),
        "boundary_violation": False,
        "scar_pressure": 0.0,
        "elapsed_hours": 0.0,
    }
    params.update(kwargs)
    return tolerance.update(**params)


class ToleranceTests(unittest.TestCase):
    def test_baseline_is_regulated_and_load_is_near_zero(self):
        tolerance = ToleranceState()
        for _ in range(4):
            update(tolerance, BASE)
        self.assertEqual(tolerance.zone, ZONE_REGULATED)
        self.assertLess(tolerance.load, 0.01)
        self.assertGreater(tolerance.capacity, 0.0)

    def test_high_trust_and_love_expand_capacity(self):
        low = ToleranceState()
        high = ToleranceState()
        update(low, make_state(trust=0.1, love=0.1),
               trust_target_value=0.1)
        update(high, make_state(trust=0.9, love=0.9),
               trust_target_value=0.9)
        self.assertGreater(high.capacity, low.capacity)

    def test_high_neuroticism_narrows_capacity(self):
        low_n = ToleranceState()
        high_n = ToleranceState()
        update(low_n, BASE, neuroticism=0.2)
        update(high_n, BASE, neuroticism=0.9)
        self.assertGreater(low_n.capacity, high_n.capacity)

    def test_slow_channels_do_not_double_count_load(self):
        tolerance = ToleranceState()
        state = make_state(trust=0.9, love=0.9)
        update(tolerance, state, trust_target_value=0.9)
        self.assertLess(tolerance.load, 0.01)
        self.assertEqual(tolerance.zone, ZONE_REGULATED)

    def test_negative_event_raises_load_and_enters_hyper(self):
        tolerance = ToleranceState()
        stressed = make_state(
            sadness=0.8, anger=0.9, fear=0.9,
            disgust=0.7, guilt=0.5, trust=0.10,
        )
        for _ in range(2):
            update(
                tolerance, stressed,
                trust_target_value=0.5,
                shock_channels=("anger", "fear", "disgust"),
                boundary_violation=True,
            )
        self.assertEqual(tolerance.zone, ZONE_HYPER)
        self.assertGreater(tolerance.load, tolerance.capacity)

    def test_sustained_withdrawal_enters_hypo_not_hyper(self):
        tolerance = ToleranceState()
        collapsed = make_state(
            sadness=1.2, guilt=1.0, fear=0.7,
            longing=1.2, anger=0.05, disgust=0.0, trust=0.02,
        )
        for _ in range(2):
            update(
                tolerance, collapsed,
                trust_target_value=0.41,
                shock_channels=("sadness", "fear", "guilt", "surprise"),
            )
        self.assertEqual(tolerance.zone, ZONE_HYPO)
        self.assertGreater(tolerance.load, tolerance.capacity)

    def test_low_load_never_becomes_hypo(self):
        tolerance = ToleranceState()
        for _ in range(6):
            update(tolerance, BASE, elapsed_hours=1.0)
        self.assertEqual(tolerance.zone, ZONE_REGULATED)

    def test_zone_switch_requires_two_ticks(self):
        tolerance = ToleranceState()
        stressed = make_state(
            sadness=0.8, anger=0.9, fear=0.9,
            disgust=0.7, guilt=0.5, trust=0.10,
        )
        update(tolerance, stressed, trust_target_value=0.5,
               shock_channels=("anger", "fear", "disgust"))
        self.assertEqual(tolerance.zone, ZONE_REGULATED)  # pending, not switched
        self.assertEqual(tolerance.pending_ticks, 1)

        update(tolerance, stressed, trust_target_value=0.5,
               shock_channels=("anger", "fear", "disgust"))
        self.assertEqual(tolerance.zone, ZONE_HYPER)

    def test_recovery_requires_hysteresis_and_time(self):
        tolerance = ToleranceState()
        stressed = make_state(
            sadness=0.8, anger=0.9, fear=0.9,
            disgust=0.7, guilt=0.5, trust=0.10,
        )
        for _ in range(2):
            update(tolerance, stressed, trust_target_value=0.5,
                   shock_channels=("anger", "fear", "disgust"))
        self.assertEqual(tolerance.zone, ZONE_HYPER)

        update(tolerance, BASE, elapsed_hours=24.0)
        self.assertEqual(tolerance.zone, ZONE_HYPER)  # pending recovery
        update(tolerance, BASE, elapsed_hours=24.0)
        self.assertEqual(tolerance.zone, ZONE_REGULATED)

    def test_load_decays_without_new_events(self):
        tolerance = ToleranceState()
        stressed = make_state(
            sadness=0.8, anger=0.9, fear=0.9,
            disgust=0.7, guilt=0.5, trust=0.10,
        )
        update(tolerance, stressed, trust_target_value=0.5,
               shock_channels=("anger", "fear", "disgust"))
        peak = tolerance.load
        update(tolerance, BASE, elapsed_hours=4.0)
        self.assertLess(tolerance.load, peak)

    def test_boundary_and_shock_add_load(self):
        plain = ToleranceState()
        loaded = ToleranceState()
        update(plain, BASE)
        update(
            loaded, BASE,
            shock_channels=("anger", "fear", "disgust", "sadness"),
            boundary_violation=True,
        )
        self.assertGreater(loaded.load, plain.load)

    def test_mode_multipliers(self):
        tolerance = ToleranceState()
        tolerance.zone = ZONE_HYPER
        self.assertGreater(tolerance.negative_gain, 1.0)
        self.assertGreater(tolerance.interaction_scale, 1.0)
        self.assertGreater(tolerance.decay_scale_for("fear"), 1.0)
        self.assertEqual(tolerance.decay_scale_for("joy"), 1.0)

        tolerance.zone = ZONE_HYPO
        self.assertLess(tolerance.negative_gain, 1.0)
        self.assertLess(tolerance.interaction_scale, 1.0)

    def test_serialization_roundtrip(self):
        tolerance = ToleranceState()
        tolerance.zone = ZONE_HYPO
        tolerance.load = 0.9
        tolerance.capacity = 0.8
        tolerance.overload_ratio = 1.125
        tolerance.pending_zone = ZONE_HYPER
        tolerance.pending_ticks = 1

        restored = ToleranceState.from_dict(tolerance.to_dict())
        self.assertEqual(restored.zone, ZONE_HYPO)
        self.assertAlmostEqual(restored.load, 0.9)
        self.assertAlmostEqual(restored.capacity, 0.8)
        self.assertEqual(restored.pending_zone, ZONE_HYPER)
        self.assertEqual(restored.pending_ticks, 1)

    def test_invalid_serialized_zone_falls_back(self):
        restored = ToleranceState.from_dict({"zone": "overloaded"})
        self.assertEqual(restored.zone, ZONE_REGULATED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
