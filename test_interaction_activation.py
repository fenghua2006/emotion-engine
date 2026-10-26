"""L1.1 交互激活语义回归测试。

修复的核心不是“调低某个权重”，而是区分：
  - 人格基线 = 底色，不是正在发生的情绪；
  - 高于基线的激活 = 真正参与交互的情绪活动。

覆盖三层：
  1. 纯函数 apply_interactions 的 relative / legacy 两种模式；
  2. 参考线 FastLine 的空闲与事件行为；
  3. 生产引擎 EmotionEngine 的集成行为。

L1 golden 在 test_l1_fast_channels.py；这里只测语义，不重复 golden。
"""

from __future__ import annotations

import unittest

from core import (
    Appraisal,
    Channel,
    EmotionalState,
    Personality,
    apply_interactions,
    saturate,
)
from engine import (
    EmotionEngine,
    MemoryStore,
    SensitizationStore,
)
from fastline import FastLine, SIX_CHANNELS


def make_engine(*, legacy: bool = False, **personality) -> EmotionEngine:
    pers = Personality(**personality)
    state = EmotionalState()
    for channel, value in pers.baseline().items():
        setattr(state, channel.value, value)
    return EmotionEngine(
        state=state,
        personality=pers,
        memory=MemoryStore(),
        scars=SensitizationStore(),
        legacy_interactions=legacy,
    )


def make_state(**overrides) -> EmotionalState:
    state = EmotionalState()
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


class InteractionFunctionTests(unittest.TestCase):
    def test_relative_mode_is_noop_at_baseline(self):
        personality = Personality()
        baseline = personality.baseline()
        state = make_state(**{ch.value: value for ch, value in baseline.items()})
        before = state.to_dict()

        blends = apply_interactions(state, baseline=baseline)

        self.assertEqual(blends, [])
        self.assertEqual(state.to_dict(), before)

    def test_legacy_mode_drifts_at_baseline(self):
        # 旧行为对照：surprise 0.10 / disgust 0.06 高于 0.05 门槛。
        personality = Personality()
        baseline = personality.baseline()
        state = make_state(**{ch.value: value for ch, value in baseline.items()})

        apply_interactions(state)

        self.assertGreater(state.joy, baseline[Channel.JOY])
        self.assertGreater(state.anger, baseline[Channel.ANGER])
        # fear 在旧行为里不一定上升：trust→fear 的抑制会抵消
        # surprise→fear 的放大。这里只声明旧行为确实会改变通道。
        self.assertNotEqual(state.fear, baseline[Channel.FEAR])

    def test_relative_mode_uses_only_activation_above_baseline(self):
        personality = Personality()
        baseline = personality.baseline()
        state = make_state(**{ch.value: value for ch, value in baseline.items()})
        # surprise 高于基线 0.2；joy 高于基线 0.2，满足共激活。
        state.surprise = baseline[Channel.SURPRISE] + 0.2
        state.joy = baseline[Channel.JOY] + 0.2
        joy_before = state.joy

        apply_interactions(state, baseline=baseline)

        expected = 0.2 * 0.15 * saturate(joy_before)
        self.assertAlmostEqual(state.joy - joy_before, expected, delta=1e-12)

    def test_surprise_does_not_create_joy_when_joy_is_at_baseline(self):
        personality = Personality()
        baseline = personality.baseline()
        state = make_state(**{ch.value: value for ch, value in baseline.items()})
        state.surprise = baseline[Channel.SURPRISE] + 0.5

        apply_interactions(state, baseline=baseline)

        self.assertEqual(state.joy, baseline[Channel.JOY])
        self.assertEqual(state.fear, baseline[Channel.FEAR])

    def test_suppress_rule_fires_from_active_source_at_baseline_target(self):
        # 抑制规则不能要求目标共激活：愤怒应当能压低处于基线的信任。
        personality = Personality()
        baseline = personality.baseline()
        state = make_state(**{ch.value: value for ch, value in baseline.items()})
        state.anger = baseline[Channel.ANGER] + 0.2
        trust_before = state.trust

        apply_interactions(state, baseline=baseline)

        self.assertLess(state.trust, trust_before)


class ReferenceLineTests(unittest.TestCase):
    def test_idle_ticks_keep_all_six_channels_at_baseline(self):
        line = FastLine()
        for _ in range(50):
            line.step(None, elapsed_minutes=10.0)

        baseline = line.personality.baseline()
        for ch in SIX_CHANNELS:
            self.assertAlmostEqual(
                getattr(line.state, ch.value), baseline[ch], delta=1e-9,
                msg=ch.value,
            )

    def test_expected_negative_event_does_not_add_joy(self):
        event = Appraisal(
            goal_relevance=1.0,
            goal_conduciveness=-0.8,
            expectedness=1.0,
            other_agency=0.5,
            coping_potential=0.0,
            social_evaluation=0.0,
        )
        step = FastLine().step(event, elapsed_minutes=10.0)
        baseline = FastLine().personality.baseline()
        self.assertEqual(step.raw_appraisal["joy"], 0.0)
        self.assertAlmostEqual(
            step.values["joy"], baseline[Channel.JOY], delta=1e-12,
        )


class EngineIntegrationTests(unittest.TestCase):
    def test_idle_ticks_do_not_raise_joy_or_anger(self):
        engine = make_engine()
        now = engine.state._last_update
        for _ in range(100):
            now += 600
            engine.tick(None, now=now)

        baseline = engine.personality.baseline()
        self.assertAlmostEqual(
            engine.state.joy, baseline[Channel.JOY], delta=1e-9,
        )
        self.assertAlmostEqual(
            engine.state.anger, baseline[Channel.ANGER], delta=1e-9,
        )
        self.assertAlmostEqual(
            engine.state.sadness, baseline[Channel.SADNESS], delta=1e-9,
        )

    def test_legacy_engine_reproduces_idle_floor(self):
        engine = make_engine(legacy=True)
        now = engine.state._last_update
        for _ in range(100):
            now += 600
            engine.tick(None, now=now)

        baseline = engine.personality.baseline()
        self.assertGreater(engine.state.joy, baseline[Channel.JOY] + 0.01)
        self.assertGreater(engine.state.anger, baseline[Channel.ANGER] + 0.01)

    def test_expected_negative_event_does_not_add_joy(self):
        event = Appraisal(
            goal_relevance=1.0,
            goal_conduciveness=-0.8,
            expectedness=1.0,
            other_agency=0.5,
            coping_potential=0.0,
            social_evaluation=0.0,
        )
        engine = make_engine()
        baseline = engine.personality.baseline()
        engine.tick(event, now=engine.state._last_update + 60)
        self.assertAlmostEqual(
            engine.state.joy, baseline[Channel.JOY], delta=1e-9,
        )

    def test_legacy_engine_expected_negative_event_gains_joy(self):
        event = Appraisal(
            goal_relevance=1.0,
            goal_conduciveness=-0.8,
            expectedness=1.0,
            other_agency=0.5,
            coping_potential=0.0,
            social_evaluation=0.0,
        )
        engine = make_engine(legacy=True)
        baseline = engine.personality.baseline()
        engine.tick(event, now=engine.state._last_update + 60)
        self.assertGreater(engine.state.joy, baseline[Channel.JOY] + 0.001)


if __name__ == "__main__":
    unittest.main(verbosity=2)
