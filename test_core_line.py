"""L0 核心情绪线回归测试。

目标不是验证“情绪理论对不对”，而是锁住现有核心公式的行为，
让 L1 以后的新层能在不悄悄改变底层曲线的前提下叠加。
"""

import ast
import math
import pathlib
import unittest

import core
from core import (
    ABSENCE_CHANNELS,
    Appraisal,
    Channel,
    EmotionalState,
    FAST_CHANNELS,
    HALF_LIFE,
    NEGATIVE_CHANNELS,
    Personality,
    SLOW_CHANNELS,
    StepTrace,
    appraise,
    damage_trust,
    decay,
    gate_appraisal,
    grow_slow_channels,
    return_to_baseline,
    saturate,
    trust_target,
)
from engine import (
    EmotionEngine,
    MemoryStore,
    SensitizationStore,
    _build_llm_prompt,
    respond,
)


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
        social_evaluation=-0.4,
    )


class CoreTaxonomyTests(unittest.TestCase):
    def test_channel_groups_cover_all_channels_without_overlap(self):
        grouped = FAST_CHANNELS + SLOW_CHANNELS + ABSENCE_CHANNELS
        self.assertEqual(set(grouped), set(Channel))
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual(len(Channel), 10)

    def test_negative_channels_are_subset_of_fast_channels(self):
        self.assertEqual(
            set(NEGATIVE_CHANNELS),
            {Channel.SADNESS, Channel.ANGER, Channel.FEAR,
             Channel.DISGUST, Channel.GUILT},
        )
        self.assertTrue(set(NEGATIVE_CHANNELS).issubset(set(FAST_CHANNELS)))

    def test_slow_channels_have_no_auto_decay(self):
        for ch in SLOW_CHANNELS:
            self.assertIsNone(HALF_LIFE[ch])


class CoreMathTests(unittest.TestCase):
    def test_decay_with_distance_shortens_effective_half_life(self):
        # baseline=1, value=2 → distance=1 → effective half-life=45min.
        value = decay(2.0, 90, 45, baseline=1.0)
        self.assertAlmostEqual(value, 1.5)

    def test_decay_does_not_touch_slow_channels_or_zero_elapsed(self):
        self.assertEqual(decay(0.7, None, 999, baseline=0.2), 0.7)
        self.assertEqual(decay(0.7, 90, 0, baseline=0.2), 0.7)

    def test_return_to_baseline_pull_is_capped(self):
        # 最多拉回距离的 30%，不会一帧瞬移。
        self.assertAlmostEqual(
            return_to_baseline(2.0, 1.0, 10_000),
            1.7,
        )
        self.assertEqual(return_to_baseline(0.5, 0.5, 60), 0.5)

    def test_saturate_is_linear_diminishing_return(self):
        self.assertAlmostEqual(saturate(0.0), 1.0)
        self.assertAlmostEqual(saturate(1.5), 0.5)
        self.assertAlmostEqual(saturate(3.0), 0.0)
        self.assertAlmostEqual(saturate(5.0), 0.0)

    def test_trust_target_is_monotonic_and_below_one(self):
        values = [trust_target(x) for x in (0.0, 0.1, 0.3, 0.6, 0.9)]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(0.0 <= v < 1.0 for v in values))
        self.assertAlmostEqual(trust_target(0.3), 1 - 1 / (1 + 1.05))

    def test_felt_compression_is_logarithmic(self):
        state = EmotionalState(joy=0.3)
        self.assertAlmostEqual(state.felt(Channel.JOY), math.log10(1.6))
        state.joy = 0.0
        self.assertEqual(state.felt(Channel.JOY), 0.0)


class SlowChannelTests(unittest.TestCase):
    def test_trust_grows_toward_love_target_and_love_is_eroded(self):
        state = EmotionalState(love=0.5, trust=0.1)
        grow_slow_channels(state, events_today=0, positive_count=0,
                           elapsed_hours=1.0)
        # target=0.636…, trust 仍远低于 target，love 被信任缺口侵蚀。
        self.assertGreater(state.trust, 0.1)
        self.assertGreater(state.trust, 0.11)
        self.assertLess(state.love, 0.5)

    def test_positive_events_grow_love_when_trust_is_healthy(self):
        state = EmotionalState(love=0.3, trust=0.5)
        before = state.love
        grow_slow_channels(state, events_today=1, positive_count=2,
                           elapsed_hours=1.0)
        self.assertGreater(state.love, before)

    def test_damage_trust_halves_at_full_severity(self):
        state = EmotionalState(love=0.5, trust=0.8)
        damage_trust(state, 1.0)
        self.assertAlmostEqual(state.trust, 0.4)
        self.assertAlmostEqual(state.love, 0.425)

    def test_mild_betrayal_does_not_erode_love(self):
        state = EmotionalState(love=0.5, trust=0.8)
        damage_trust(state, 0.4)
        self.assertAlmostEqual(state.love, 0.5)


class AppraisalTests(unittest.TestCase):
    def test_negative_event_produces_mixed_activation(self):
        raw = appraise(bad_event())
        for ch in (Channel.SADNESS, Channel.ANGER, Channel.FEAR,
                   Channel.DISGUST, Channel.SURPRISE):
            self.assertGreater(raw[ch], 0.0, ch.value)
        # 混合情绪：负面事件也允许微量 joy 痕迹。
        self.assertGreater(raw[Channel.JOY], 0.0)

    def test_guilt_requires_self_agency(self):
        external = Appraisal(
            goal_relevance=1.0, goal_conduciveness=-1.0,
            expectedness=0.0, coping_potential=0.5,
            other_agency=1.0,
        )
        internal = Appraisal(
            goal_relevance=1.0, goal_conduciveness=-1.0,
            expectedness=0.0, coping_potential=0.5,
            other_agency=0.0,
        )
        self.assertEqual(appraise(external)[Channel.GUILT], 0.0)
        self.assertGreater(appraise(internal)[Channel.GUILT], 0.0)

    def test_gate_low_trust_boundary_raises_disgust_and_suppresses_joy(self):
        raw = {Channel.JOY: 0.6, Channel.DISGUST: 0.0}
        gated = gate_appraisal(raw, trust=0.1, love=0.2,
                               boundary_violation=True)
        self.assertGreater(gated[Channel.DISGUST], 0.5)
        self.assertLess(gated[Channel.JOY], raw[Channel.JOY])

    def test_gate_high_trust_reduces_fear(self):
        raw = {Channel.FEAR: 0.8}
        low = gate_appraisal(raw, trust=0.1, love=0.2)
        high = gate_appraisal(raw, trust=0.9, love=0.2)
        self.assertLess(high[Channel.FEAR], low[Channel.FEAR])


class PersonalityTests(unittest.TestCase):
    def test_baseline_contains_all_channels_and_zero_for_absence(self):
        baseline = Personality(extraversion=0.8, neuroticism=0.7).baseline()
        self.assertEqual(set(baseline), set(Channel))
        self.assertEqual(baseline[Channel.LONGING], 0.0)
        self.assertEqual(baseline[Channel.GUILT], 0.0)
        self.assertGreater(baseline[Channel.JOY], 0.4)

    def test_baseline_is_locked(self):
        pers = Personality()
        first = pers.baseline()
        first[Channel.JOY] = 99.0
        self.assertNotEqual(pers.baseline()[Channel.JOY], 99.0)


class StepTraceTests(unittest.TestCase):
    def test_trace_mark_copies_snapshot(self):
        state = EmotionalState(joy=0.4)
        trace = StepTrace()
        trace.mark("before", state)
        state.joy = 0.9
        self.assertAlmostEqual(trace.before["joy"], 0.4)

    def test_trace_unknown_phase_only_adds_note(self):
        trace = StepTrace()
        trace.mark("l2_atmosphere", EmotionalState())
        self.assertEqual(trace.notes, ["unknown phase: l2_atmosphere"])
        self.assertEqual(trace.before, {})

    def test_trace_does_not_change_engine_result(self):
        plain = make_engine()
        traced = make_engine()
        now = max(plain.state._last_update, traced.state._last_update)
        plain.state._last_update = now
        traced.state._last_update = now

        for step in range(3):
            at = now + step * 60
            plain_result = plain.tick(bad_event(), now=at)
            traced_result = traced.tick(bad_event(), now=at, trace=True)
            traced_result.pop("trace")
            self.assertEqual(plain_result, traced_result)
            self.assertEqual(plain.state.to_dict(), traced.state.to_dict())

    def test_trace_contains_ordered_phases_and_metadata(self):
        engine = make_engine()
        start = engine.state._last_update
        result = engine.tick(bad_event(), now=start + 60, trace=True)
        trace = result["trace"]

        for phase in ("before", "after_decay", "after_slow_channels",
                      "after_appraisal", "after_atmosphere",
                      "after_interaction", "final"):
            self.assertIn(phase, trace)
            self.assertEqual(set(trace[phase]), {ch.value for ch in Channel})
        self.assertEqual(trace["elapsed_minutes"], 1.0)
        self.assertIn("anger", trace["raw_appraisal"])
        self.assertIn("tolerance_zone", trace)
        self.assertEqual(trace["final"], result["state"])

    def test_trace_exposes_known_l0_ordering_risks(self):
        # 这两条是 L0 刻意保持的现有行为，不是设计目标。
        # L1 修正顺序后，应同步删除此断言并替换为回归测试。
        engine = make_engine()
        result = engine.tick(
            bad_event(),
            now=engine.state._last_update + 60,
            trace=True,
        )
        notes = " ".join(result["trace"]["notes"])
        self.assertIn("atmosphere gate mutates gated after state write", notes)
        self.assertIn("shock density sampled before shock detection", notes)

    def test_clock_backwards_is_clamped_and_recorded(self):
        engine = make_engine()
        start = engine.state._last_update
        result = engine.tick(now=start - 300, trace=True)
        self.assertEqual(result["trace"]["elapsed_minutes"], 0.0)
        self.assertIn("clock_went_backwards", " ".join(result["trace"]["notes"]))


class CompatibilityTests(unittest.TestCase):
    def test_core_has_no_upper_layer_imports(self):
        tree = ast.parse(pathlib.Path(core.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {"engine", "tolerance", "memory", "sqlite3",
                     "requests", "httpx", "urllib"}
        self.assertFalse(imported & forbidden)

    def test_engine_reexports_core_types(self):
        import engine
        self.assertIs(engine.EmotionalState, core.EmotionalState)
        self.assertIs(engine.Personality, core.Personality)
        self.assertIs(engine.Appraisal, core.Appraisal)
        self.assertIs(engine.Channel, core.Channel)
        self.assertIs(engine.StepTrace, core.StepTrace)

    def test_expression_layer_still_reads_core_state(self):
        engine = make_engine()
        prompt = _build_llm_prompt(engine, "你还好吗", "Furina", "测试角色", [])
        self.assertIn("Furina", prompt)
        local = respond(engine, "test")
        self.assertIn("_state", local)
        self.assertEqual(set(local["_state"]), {ch.value for ch in Channel})


if __name__ == "__main__":
    unittest.main(verbosity=2)
