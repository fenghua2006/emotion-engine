"""L7 v0：四维情绪感染纯函数与 ContagionPair 单元测试。"""

import math
import unittest

from contagion import (
    CONTAGION_DIMENSIONS,
    CONTAGION_NEUTRAL,
    DEFAULT_CONTAGION_CAP,
    MAX_CONTAGION_CAP,
    ContagionPair,
    derive_contagion_bias,
    sanitize_contagion_bias,
)
from core import Appraisal, EmotionalState, Personality
from engine import EmotionEngine, MemoryStore, SensitizationStore


NOW = 1_800_000_000.0


def make_engine(personality=None):
    personality = personality or Personality()
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


class ContagionDeriveTest(unittest.TestCase):
    def test_dimensions_and_neutral_values(self):
        self.assertEqual(
            set(CONTAGION_DIMENSIONS),
            {
                "goal_conduciveness", "expectedness",
                "coping_potential", "social_evaluation",
            },
        )
        self.assertEqual(CONTAGION_NEUTRAL["goal_conduciveness"], 0.0)
        self.assertEqual(CONTAGION_NEUTRAL["expectedness"], 0.5)
        self.assertEqual(CONTAGION_NEUTRAL["coping_potential"], 0.5)
        self.assertEqual(CONTAGION_NEUTRAL["social_evaluation"], 0.0)

    def test_neutral_appraisal_and_non_transmitted_dimensions(self):
        self.assertEqual(derive_contagion_bias(Appraisal()), {})
        # goal_relevance / other_agency 不参与跨角色传输。
        only_context = Appraisal(goal_relevance=1.0, other_agency=1.0)
        self.assertEqual(derive_contagion_bias(only_context), {})

    def test_goal_conduciveness_sign(self):
        positive = derive_contagion_bias(Appraisal(goal_conduciveness=1.0))
        negative = derive_contagion_bias(Appraisal(goal_conduciveness=-1.0))
        self.assertAlmostEqual(positive["goal_conduciveness"], 0.08)
        self.assertAlmostEqual(negative["goal_conduciveness"], -0.08)

    def test_expectedness_direction(self):
        low = derive_contagion_bias(Appraisal(expectedness=0.0))
        middle = derive_contagion_bias(Appraisal(expectedness=0.5))
        high = derive_contagion_bias(Appraisal(expectedness=1.0))
        self.assertAlmostEqual(low["expectedness"], -0.08)
        self.assertEqual(middle, {})
        self.assertAlmostEqual(high["expectedness"], 0.08)

    def test_coping_and_social_directions(self):
        low_coping = derive_contagion_bias(
            Appraisal(coping_potential=0.0),
        )
        high_coping = derive_contagion_bias(
            Appraisal(coping_potential=1.0),
        )
        negative_social = derive_contagion_bias(
            Appraisal(social_evaluation=-1.0),
        )
        positive_social = derive_contagion_bias(
            Appraisal(social_evaluation=1.0),
        )
        self.assertAlmostEqual(low_coping["coping_potential"], -0.08)
        self.assertAlmostEqual(high_coping["coping_potential"], 0.08)
        self.assertAlmostEqual(
            negative_social["social_evaluation"], -0.08,
        )
        self.assertAlmostEqual(
            positive_social["social_evaluation"], 0.08,
        )

    def test_default_cap_bounds_each_dimension(self):
        bias = derive_contagion_bias(Appraisal(
            goal_conduciveness=1.0,
            expectedness=0.0,
            coping_potential=0.0,
            social_evaluation=-1.0,
        ))
        for dimension in CONTAGION_DIMENSIONS:
            self.assertLessEqual(
                abs(bias[dimension]), DEFAULT_CONTAGION_CAP + 1e-12,
            )

    def test_custom_gain_and_cap(self):
        weak_cap = derive_contagion_bias(
            Appraisal(goal_conduciveness=1.0),
            gain=0.5, cap=0.05,
        )
        hard_cap = derive_contagion_bias(
            Appraisal(goal_conduciveness=1.0),
            gain=10.0, cap=10.0,
        )
        zero_cap = derive_contagion_bias(
            Appraisal(goal_conduciveness=1.0),
            gain=1.0, cap=-1.0,
        )
        self.assertAlmostEqual(weak_cap["goal_conduciveness"], 0.05)
        self.assertAlmostEqual(
            hard_cap["goal_conduciveness"], MAX_CONTAGION_CAP,
        )
        self.assertEqual(zero_cap, {})

    def test_sanitize_drops_unknown_and_invalid_values(self):
        cleaned = sanitize_contagion_bias({
            "goal_conduciveness": 2.0,
            "expectedness": -2.0,
            "coping_potential": True,
            "social_evaluation": float("nan"),
            "goal_relevance": 1.0,
            "other_agency": 0.5,
            "unknown": 1.0,
        })
        self.assertEqual(cleaned, {
            "goal_conduciveness": MAX_CONTAGION_CAP,
            "expectedness": -MAX_CONTAGION_CAP,
        })

    def test_sanitize_all_invalid_returns_none(self):
        self.assertIsNone(sanitize_contagion_bias(None))
        self.assertIsNone(sanitize_contagion_bias({}))
        self.assertIsNone(sanitize_contagion_bias("not a mapping"))
        self.assertIsNone(sanitize_contagion_bias({
            "goal_conduciveness": float("inf"),
            "expectedness": "0.1",
        }))
        self.assertIsNone(sanitize_contagion_bias({
            "unknown": 1.0,
        }))


class ContagionPairTest(unittest.TestCase):
    def test_rejects_same_engine(self):
        engine = make_engine()
        with self.assertRaises(ValueError):
            ContagionPair(engine, engine)

    def test_rejects_shared_mutable_substructures(self):
        for attribute in ("state", "memory", "scars", "tolerance"):
            source = make_engine()
            target = make_engine()
            setattr(target, attribute, getattr(source, attribute))
            with self.subTest(attribute=attribute):
                with self.assertRaises(ValueError):
                    ContagionPair(source, target)

    def test_allows_shared_personality_without_mutation(self):
        personality = Personality(openness=0.7, neuroticism=0.6)
        source = make_engine(personality)
        target = make_engine(personality)
        pair = ContagionPair(source, target)
        before = (
            personality.openness,
            personality.conscientiousness,
            personality.extraversion,
            personality.agreeableness,
            personality.neuroticism,
        )

        pair.tick_pair(
            Appraisal(goal_relevance=0.8, goal_conduciveness=-0.6),
            Appraisal(goal_relevance=0.8, goal_conduciveness=0.4),
            now=NOW,
        )

        after = (
            personality.openness,
            personality.conscientiousness,
            personality.extraversion,
            personality.agreeableness,
            personality.neuroticism,
        )
        self.assertEqual(before, after)

    def test_tick_target_with_neutral_source_matches_plain_tick(self):
        source = make_engine()
        target = make_engine()
        control = make_engine()
        pair = ContagionPair(source, target)
        appraisal = Appraisal(
            goal_relevance=0.7,
            goal_conduciveness=0.3,
            expectedness=0.6,
            coping_potential=0.6,
            other_agency=0.4,
            social_evaluation=0.2,
        )

        paired = pair.tick_target(
            appraisal, Appraisal(goal_relevance=0.5), now=NOW,
        )
        plain = control.tick(appraisal, now=NOW)

        self.assertEqual(paired, plain)
        self.assertNotIn("contagion_bias", paired)

    def test_derived_bias_is_finite(self):
        bias = derive_contagion_bias(Appraisal(
            goal_conduciveness=0.7,
            expectedness=0.1,
            coping_potential=0.2,
            social_evaluation=-0.4,
        ))
        self.assertTrue(bias)
        for value in bias.values():
            self.assertTrue(math.isfinite(value))


if __name__ == "__main__":
    unittest.main()
