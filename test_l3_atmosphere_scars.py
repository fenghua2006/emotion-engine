"""L3 氛围与伤疤回归测试（unittest，无外部依赖）。

覆盖：
  - atmosphere 纯函数：目标、单步漂移、clamp、调制因子；
  - scar 纯函数：三通道限制、60% 上限、negative gate；
  - corrected / legacy 两种 tick 顺序的 A/B；
  - shock 密度非零且延后应用；
  - 无伤疤 / 有伤疤对照；
  - 快照 flag 与 pre-L3 精确兼容值。
"""

from __future__ import annotations

import ast
import math
import os
import pathlib
import tempfile
import unittest

import atmosphere
from atmosphere import (
    ATMOSPHERE_MAX,
    ATMOSPHERE_MIN,
    MAX_SCAR_SHIFT,
    apply_atmosphere_modulation,
    apply_scar_amplification,
    atmosphere_target,
    clamp_atmosphere,
    clamp_scar_shift,
    drift_atmosphere,
)
from core import (
    Appraisal,
    Channel,
    EmotionalState,
    FAST_CHANNELS,
    Personality,
)
from engine import (
    EmotionEngine,
    MemoryStore,
    SensitizationPattern,
    SensitizationStore,
    load_snapshot,
    save_snapshot,
)


def make_engine(*, legacy_atmosphere=False, atmosphere=-0.3,
                scars=None, **personality):
    pers = Personality(**personality)
    state = EmotionalState()
    for channel, value in pers.baseline().items():
        setattr(state, channel.value, value)
    return EmotionEngine(
        state=state,
        personality=pers,
        memory=MemoryStore(),
        scars=SensitizationStore() if scars is None else scars,
        atmosphere=atmosphere,
        legacy_atmosphere=legacy_atmosphere,
    )


def severe_event():
    return Appraisal(
        goal_relevance=0.9,
        goal_conduciveness=-0.9,
        expectedness=0.2,
        other_agency=0.9,
        coping_potential=0.1,
        social_evaluation=-0.4,
    )


def positive_event():
    return Appraisal(
        goal_relevance=0.8,
        goal_conduciveness=0.8,
        expectedness=0.5,
        other_agency=0.8,
        coping_potential=0.5,
        social_evaluation=0.6,
    )


def run_tick(engine, appraisal=None, *, trace=True, elapsed_minutes=60.0,
             boundary_violation=False):
    now = engine.state._last_update + elapsed_minutes
    return engine.tick(
        appraisal,
        boundary_violation=boundary_violation,
        now=now,
        trace=trace,
    )


class AtmosphereMathTests(unittest.TestCase):
    def test_clamp_bounds(self):
        self.assertEqual(clamp_atmosphere(-1.7), ATMOSPHERE_MIN)
        self.assertEqual(clamp_atmosphere(2.3), ATMOSPHERE_MAX)
        self.assertEqual(clamp_atmosphere(-0.3), -0.3)
        self.assertEqual(clamp_atmosphere(float("nan")), 0.0)

    def test_target_formula_and_boundary_bonus(self):
        expected = (
            -0.6
            + 0.5 * 1.2
            + 0.25 * 0.4
        )
        self.assertAlmostEqual(
            atmosphere_target(0.5, 0.25), expected, places=12,
        )
        self.assertAlmostEqual(
            atmosphere_target(0.5, 0.25, boundary_violation=True),
            expected + 0.3, places=12,
        )

    def test_drift_step_is_capped(self):
        # target > current by far → exactly +0.1
        self.assertAlmostEqual(
            drift_atmosphere(0.0, shock_density=1.0, arousal=1.0),
            0.1, places=12,
        )
        # target < current by far → exactly -0.1
        self.assertAlmostEqual(
            drift_atmosphere(0.5, shock_density=0.0, arousal=0.0),
            0.4, places=12,
        )

    def test_drift_clamps_upper_and_lower(self):
        self.assertEqual(
            drift_atmosphere(0.95, shock_density=1.0, arousal=1.0),
            ATMOSPHERE_MAX,
        )
        self.assertEqual(
            drift_atmosphere(
                -1.2, shock_density=0.0, arousal=0.0,
                boundary_violation=True,
            ),
            ATMOSPHERE_MIN,
        )
        self.assertGreater(
            drift_atmosphere(
                0.95, shock_density=1.0, arousal=1.0,
                boundary_violation=True, clamp=False,
            ),
            ATMOSPHERE_MAX,
        )

    def test_tense_multipliers(self):
        gated = {
            Channel.JOY: 0.5,
            Channel.SADNESS: 0.4,
            Channel.FEAR: 0.3,
            Channel.ANGER: 0.2,
            Channel.DISGUST: 0.1,
            Channel.SURPRISE: 0.2,
            Channel.GUILT: 0.3,
            Channel.LOVE: 0.4,
        }
        result, multipliers = apply_atmosphere_modulation(gated, 0.5)

        self.assertAlmostEqual(result[Channel.JOY], 0.5 * 0.85)
        for channel in (Channel.SADNESS, Channel.FEAR, Channel.ANGER):
            self.assertAlmostEqual(result[channel], gated[channel] * 1.2)
            self.assertAlmostEqual(multipliers[channel.value], 1.2)
        for channel in (Channel.DISGUST, Channel.SURPRISE,
                        Channel.GUILT, Channel.LOVE):
            self.assertEqual(result[channel], gated[channel])
        self.assertNotIn("disgust", multipliers)

    def test_relaxed_multipliers(self):
        gated = {
            Channel.JOY: 0.5,
            Channel.SADNESS: 0.4,
            Channel.FEAR: 0.3,
            Channel.ANGER: 0.2,
        }
        result, multipliers = apply_atmosphere_modulation(gated, -0.5)

        self.assertAlmostEqual(result[Channel.JOY], 0.5 * 1.1)
        for channel in (Channel.SADNESS, Channel.FEAR, Channel.ANGER):
            self.assertAlmostEqual(result[channel], gated[channel] * 0.85)
            self.assertAlmostEqual(multipliers[channel.value], 0.85)

    def test_zero_atmosphere_is_noop_and_returns_copy(self):
        gated = {Channel.JOY: 0.5, Channel.SADNESS: 0.4}
        result, multipliers = apply_atmosphere_modulation(gated, 0.0)
        self.assertEqual(result, gated)
        self.assertIsNot(result, gated)
        self.assertEqual(multipliers, {})
        result[Channel.JOY] = 0.0
        self.assertEqual(gated[Channel.JOY], 0.5)


class ScarMathTests(unittest.TestCase):
    def setUp(self):
        self.gated = {
            Channel.JOY: 0.5,
            Channel.SADNESS: 0.4,
            Channel.FEAR: 0.3,
            Channel.ANGER: 0.2,
            Channel.DISGUST: 0.1,
            Channel.SURPRISE: 0.2,
            Channel.GUILT: 0.3,
        }

    def test_only_three_channels_are_amplified(self):
        result, effective, amplified = apply_scar_amplification(
            self.gated, 0.6, -0.8,
        )
        self.assertAlmostEqual(effective, 0.6)
        self.assertEqual(
            set(amplified), {"sadness", "fear", "anger"},
        )
        for channel in (Channel.SADNESS, Channel.FEAR, Channel.ANGER):
            self.assertAlmostEqual(
                result[channel], self.gated[channel] * 1.6,
            )
        for channel in (Channel.JOY, Channel.DISGUST,
                        Channel.SURPRISE, Channel.GUILT):
            self.assertEqual(result[channel], self.gated[channel])

    def test_shift_is_capped_at_sixty_percent(self):
        result, effective, amplified = apply_scar_amplification(
            {Channel.SADNESS: 0.4}, 2.0, -0.8,
        )
        self.assertAlmostEqual(effective, MAX_SCAR_SHIFT)
        self.assertEqual(amplified, ["sadness"])
        self.assertAlmostEqual(result[Channel.SADNESS], 0.4 * 1.6)

    def test_positive_goal_is_not_amplified(self):
        result, effective, amplified = apply_scar_amplification(
            self.gated, 0.6, 0.0,
        )
        self.assertAlmostEqual(effective, 0.6)
        self.assertEqual(amplified, [])
        self.assertEqual(result, self.gated)

    def test_epsilon_and_tiny_values_are_noops(self):
        result, effective, amplified = apply_scar_amplification(
            self.gated, 0.005, -0.8,
        )
        self.assertEqual(amplified, [])
        self.assertEqual(result, self.gated)

        result, effective, amplified = apply_scar_amplification(
            {Channel.SADNESS: 0.0005}, 0.6, -0.8,
        )
        self.assertEqual(amplified, [])
        self.assertAlmostEqual(result[Channel.SADNESS], 0.0005)

    def test_store_get_shift_caps_restored_value(self):
        store = SensitizationStore()
        store.patterns["social_hurt"] = SensitizationPattern(
            tag="social_hurt", threshold_shift=2.0,
        )
        self.assertEqual(
            store.get_shift(severe_event()), MAX_SCAR_SHIFT,
        )
        self.assertEqual(clamp_scar_shift(2.0), MAX_SCAR_SHIFT)

    def test_kindling_progression(self):
        store = SensitizationStore()
        for _ in range(2):
            store.register("social_hurt", 0.0)
        self.assertEqual(
            store.patterns["social_hurt"].threshold_shift, 0.0,
        )
        store.register("social_hurt", 0.0)
        self.assertAlmostEqual(
            store.patterns["social_hurt"].threshold_shift, 0.08,
        )
        for _ in range(7):
            store.register("social_hurt", 0.0)
        self.assertAlmostEqual(
            store.patterns["social_hurt"].threshold_shift, MAX_SCAR_SHIFT,
        )

    def test_positive_counter_reduces_shift(self):
        store = SensitizationStore()
        store.patterns["social_hurt"] = SensitizationPattern(
            tag="social_hurt",
            threshold_shift=0.2,
            positive_counter=29,
        )
        store.register_positive("social_hurt")
        self.assertAlmostEqual(
            store.patterns["social_hurt"].threshold_shift, 0.1,
        )
        self.assertEqual(store.patterns["social_hurt"].positive_counter, 0)


class AtmosphereOrderingTests(unittest.TestCase):
    def test_corrected_modulates_before_state_write(self):
        corrected = make_engine(atmosphere=0.5)
        legacy = make_engine(atmosphere=0.5, legacy_atmosphere=True)
        corrected_result = run_tick(corrected, severe_event())
        legacy_result = run_tick(legacy, severe_event())

        for channel in ("sadness", "fear", "anger"):
            self.assertGreater(
                corrected_result["state"][channel],
                legacy_result["state"][channel],
            )

        corrected_trace = corrected_result["trace"]
        legacy_trace = legacy_result["trace"]
        self.assertEqual(corrected_trace["atmosphere_applied"], 0.5)
        self.assertEqual(
            corrected_trace["gated_written"],
            corrected_trace["gated_appraisal"],
        )
        self.assertNotEqual(
            legacy_trace["gated_written"]["sadness"],
            legacy_trace["gated_appraisal"]["sadness"],
        )

    def test_legacy_late_gate_does_not_change_current_state(self):
        tense = make_engine(atmosphere=0.5, legacy_atmosphere=True)
        relaxed = make_engine(atmosphere=-0.5, legacy_atmosphere=True)
        tense_result = run_tick(tense, severe_event())
        relaxed_result = run_tick(relaxed, severe_event())
        self.assertEqual(tense_result["state"], relaxed_result["state"])
        self.assertNotEqual(
            tense_result["atmosphere"], relaxed_result["atmosphere"],
        )

    def test_tense_atmosphere_increases_negative_reaction(self):
        tense = make_engine(atmosphere=0.6)
        relaxed = make_engine(atmosphere=-0.6)
        tense_result = run_tick(tense, severe_event())
        relaxed_result = run_tick(relaxed, severe_event())

        for channel in ("sadness", "fear", "anger"):
            self.assertGreater(
                tense_result["state"][channel],
                relaxed_result["state"][channel],
            )
            self.assertGreater(
                tense_result["trace"]["gated_written"][channel],
                relaxed_result["trace"]["gated_written"][channel],
            )
        self.assertLess(
            tense_result["state"]["joy"],
            relaxed_result["state"]["joy"],
        )

    def test_shock_density_is_nonzero_and_deferred(self):
        corrected = make_engine()
        result = run_tick(corrected, severe_event())
        trace = result["trace"]

        self.assertTrue(result["shock_channels"])
        self.assertAlmostEqual(
            corrected._recent_shocks[-1],
            len(result["shock_channels"]) / len(FAST_CHANNELS),
        )
        self.assertGreater(trace["atmosphere_shock_density"], 0.0)
        self.assertGreater(result["atmosphere"], -0.3)
        self.assertEqual(
            trace["atmosphere_applied"], trace["atmosphere_before"],
        )

        legacy = make_engine(legacy_atmosphere=True)
        legacy_result = run_tick(legacy, severe_event())
        self.assertEqual(legacy._recent_shocks, [0.0])
        self.assertEqual(
            legacy_result["trace"]["atmosphere_shock_density"], 0.0,
        )

    def test_corrected_clamps_before_use(self):
        engine = make_engine(atmosphere=5.0)
        result = run_tick(engine, severe_event())
        trace = result["trace"]

        self.assertEqual(trace["atmosphere_applied"], 1.0)
        self.assertAlmostEqual(
            trace["atmosphere_multipliers"]["sadness"], 1.4,
        )
        self.assertGreaterEqual(result["atmosphere"], ATMOSPHERE_MIN)
        self.assertLessEqual(result["atmosphere"], ATMOSPHERE_MAX)

    def test_legacy_does_not_clamp(self):
        engine = make_engine(atmosphere=5.0, legacy_atmosphere=True)
        result = run_tick(engine)
        self.assertGreater(result["atmosphere"], ATMOSPHERE_MAX)

    def test_trace_mode_and_notes(self):
        corrected = run_tick(make_engine(), severe_event())["trace"]
        legacy = run_tick(
            make_engine(legacy_atmosphere=True), severe_event(),
        )["trace"]

        corrected_notes = " ".join(corrected["notes"])
        legacy_notes = " ".join(legacy["notes"])
        self.assertEqual(corrected["atmosphere_mode"], "corrected")
        self.assertEqual(legacy["atmosphere_mode"], "legacy")
        self.assertIn(
            "shock detected before atmosphere update", corrected_notes,
        )
        self.assertIn(
            "atmosphere gate applied before state write", corrected_notes,
        )
        self.assertNotIn(
            "mutates gated after state write", corrected_notes,
        )
        self.assertIn(
            "atmosphere gate mutates gated after state write",
            legacy_notes,
        )
        self.assertIn(
            "shock density sampled before shock detection", legacy_notes,
        )
        self.assertNotIn("L3 corrected", legacy_notes)

    def test_trace_exposes_all_l3_fields(self):
        trace = run_tick(make_engine(), severe_event())["trace"]
        for key in (
            "atmosphere_mode", "atmosphere_before", "atmosphere_applied",
            "atmosphere_after", "atmosphere_shock_density",
            "atmosphere_arousal", "atmosphere_multipliers",
            "scar_shift", "scar_channels", "gated_written",
        ):
            self.assertIn(key, trace)

    def test_long_alternating_sequence_stays_bounded(self):
        engine = make_engine()
        for index in range(200):
            appraisal = severe_event() if index % 2 == 0 else None
            result = run_tick(engine, appraisal)
            self.assertGreaterEqual(result["atmosphere"], ATMOSPHERE_MIN)
            self.assertLessEqual(result["atmosphere"], ATMOSPHERE_MAX)
            for value in result["state"].values():
                self.assertTrue(math.isfinite(value))


class ScarComparisonTests(unittest.TestCase):
    def test_no_scar_vs_with_scar(self):
        store = SensitizationStore()
        store.patterns["social_hurt"] = SensitizationPattern(
            tag="social_hurt",
            trigger_count=5,
            threshold_shift=0.6,
        )
        plain = make_engine()
        scarred = make_engine(scars=store)

        plain_trace = run_tick(plain, severe_event())["trace"]
        scarred_trace = run_tick(scarred, severe_event())["trace"]

        scar_channels = {"sadness", "fear", "anger"}
        self.assertEqual(
            set(scarred_trace["scar_channels"]), scar_channels,
        )
        self.assertAlmostEqual(scarred_trace["scar_shift"], 0.6)

        plain_gated = plain_trace["gated_written"]
        scarred_gated = scarred_trace["gated_written"]
        for name in scar_channels:
            self.assertAlmostEqual(
                scarred_gated[name], plain_gated[name] * 1.6,
                places=9,
            )
        for name, value in plain_gated.items():
            if name not in scar_channels:
                self.assertAlmostEqual(
                    scarred_gated[name], value, places=12,
                )

    def test_corrupt_scar_shift_is_capped_in_engine(self):
        store = SensitizationStore()
        store.patterns["social_hurt"] = SensitizationPattern(
            tag="social_hurt",
            trigger_count=5,
            threshold_shift=2.0,
        )
        plain = make_engine()
        scarred = make_engine(scars=store)
        plain_trace = run_tick(plain, severe_event())["trace"]
        scarred_trace = run_tick(scarred, severe_event())["trace"]

        self.assertAlmostEqual(scarred_trace["scar_shift"], 0.6)
        for name in ("sadness", "fear", "anger"):
            ratio = (
                scarred_trace["gated_written"][name]
                / plain_trace["gated_written"][name]
            )
            self.assertLessEqual(ratio, 1.6 + 1e-12)

    def test_positive_event_is_not_amplified_by_scar(self):
        store = SensitizationStore()
        store.patterns["social_hurt"] = SensitizationPattern(
            tag="social_hurt",
            trigger_count=5,
            threshold_shift=0.6,
        )
        scarred = make_engine(scars=store)
        trace = run_tick(scarred, positive_event())["trace"]
        self.assertEqual(trace["scar_channels"], [])
        self.assertEqual(trace["scar_shift"], 0.0)


class L3SnapshotCompatibilityTests(unittest.TestCase):
    def _path(self, directory, name="l3_snapshot.db"):
        return os.path.join(directory, name)

    def test_snapshot_roundtrips_legacy_atmosphere_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            engine = make_engine(legacy_atmosphere=True, atmosphere=0.4)
            engine.state._last_update = 123456.0
            self.assertTrue(save_snapshot(engine, path, now=123456.0))

            restored = make_engine()
            self.assertTrue(load_snapshot(restored, path))
            self.assertTrue(restored.legacy_atmosphere)
            self.assertAlmostEqual(restored.atmosphere, 0.4)

    def test_snapshot_roundtrips_corrected_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            engine = make_engine(atmosphere=0.4)
            engine.state._last_update = 123456.0
            self.assertTrue(save_snapshot(engine, path, now=123456.0))

            restored = make_engine(legacy_atmosphere=True)
            self.assertTrue(load_snapshot(restored, path))
            self.assertFalse(restored.legacy_atmosphere)
            self.assertAlmostEqual(restored.atmosphere, 0.4)

    def test_load_clamps_only_in_corrected_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            corrected_path = self._path(directory, "corrected.db")
            legacy_path = self._path(directory, "legacy.db")

            corrected = make_engine(atmosphere=3.0)
            corrected.state._last_update = 123456.0
            save_snapshot(corrected, corrected_path, now=123456.0)
            restored_corrected = make_engine()
            load_snapshot(restored_corrected, corrected_path)
            self.assertEqual(restored_corrected.atmosphere, 1.0)

            legacy = make_engine(
                legacy_atmosphere=True, atmosphere=3.0,
            )
            legacy.state._last_update = 123456.0
            save_snapshot(legacy, legacy_path, now=123456.0)
            restored_legacy = make_engine()
            load_snapshot(restored_legacy, legacy_path)
            self.assertEqual(restored_legacy.atmosphere, 3.0)

    def test_pre_l3_values_are_exactly_preserved(self):
        engine = make_engine(legacy_atmosphere=True)
        result = run_tick(engine, severe_event())

        self.assertAlmostEqual(
            engine.atmosphere, -0.3774803783593781, places=12,
        )
        self.assertAlmostEqual(
            result["state"]["sadness"], 0.6125356194066094, places=12,
        )
        self.assertAlmostEqual(
            result["state"]["anger"], 0.507933595891984, places=12,
        )
        self.assertAlmostEqual(
            result["state"]["fear"], 0.6160595893163425, places=12,
        )
        self.assertEqual(
            result["shock_channels"],
            ["sadness", "anger", "fear", "disgust", "surprise"],
        )
        self.assertEqual(engine._recent_shocks, [0.0])
        notes = " ".join(result["trace"]["notes"])
        self.assertIn(
            "atmosphere gate mutates gated after state write",
            notes,
        )
        self.assertIn(
            "shock density sampled before shock detection",
            notes,
        )


class L3BoundaryTests(unittest.TestCase):
    def test_atmosphere_module_has_no_upper_layer_imports(self):
        tree = ast.parse(
            pathlib.Path(atmosphere.__file__).read_text(encoding="utf-8")
        )
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            "engine", "tolerance", "memory", "sqlite3",
            "http", "requests", "urllib", "socket", "aiohttp", "httpx",
        }
        self.assertFalse(imported & forbidden)

    def test_wake_clamps_only_in_corrected_mode(self):
        corrected = make_engine(atmosphere=3.0)
        corrected.state._last_update = 1000.0
        corrected.wake(now=1000.0 + 3600.0)
        self.assertLessEqual(corrected.atmosphere, ATMOSPHERE_MAX)

        legacy = make_engine(atmosphere=3.0, legacy_atmosphere=True)
        legacy.state._last_update = 1000.0
        legacy.wake(now=1000.0 + 3600.0)
        self.assertGreater(legacy.atmosphere, ATMOSPHERE_MAX)


if __name__ == "__main__":
    unittest.main()
