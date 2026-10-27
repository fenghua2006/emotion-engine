"""L7.2：从 trust / love 自动推导关系强度。"""

import unittest

from contagion import (
    DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
    DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
    ContagionPair,
    relationship_strength,
)
from core import Appraisal, EmotionalState, Personality
from engine import EmotionEngine, MemoryStore, SensitizationStore


NOW = 1_800_000_000.0


def make_engine(trust: float = 0.25, love: float = 0.2) -> EmotionEngine:
    personality = Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    state.trust = trust
    state.love = love
    state._last_update = NOW
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


def negative_appraisal() -> Appraisal:
    return Appraisal(
        goal_relevance=0.8,
        goal_conduciveness=-0.6,
        expectedness=0.2,
        coping_potential=0.2,
        other_agency=0.6,
        social_evaluation=-0.4,
    )


def positive_appraisal() -> Appraisal:
    return Appraisal(
        goal_relevance=0.7,
        goal_conduciveness=0.3,
        expectedness=0.6,
        coping_potential=0.6,
        other_agency=0.4,
        social_evaluation=0.2,
    )


class RelationshipStrengthTest(unittest.TestCase):
    def test_zero_and_full_bonds(self):
        self.assertEqual(relationship_strength(0.0, 0.0), 0.0)
        self.assertEqual(relationship_strength(1.0, 2.0), 1.0)

    def test_formula_uses_normalized_love(self):
        # 0.6 * 0.5 + 0.4 * (1.0 / 2.0) = 0.5
        self.assertAlmostEqual(relationship_strength(0.5, 1.0), 0.5)

    def test_clamps_out_of_range_inputs(self):
        self.assertAlmostEqual(relationship_strength(2.0, 0.0), 0.6)
        self.assertAlmostEqual(relationship_strength(-1.0, 4.0), 0.4)
        self.assertAlmostEqual(relationship_strength(0.0, 4.0), 0.4)

    def test_invalid_inputs_are_zero(self):
        for value in (None, float("nan"), float("inf"), True, "0.5"):
            with self.subTest(value=value):
                self.assertEqual(relationship_strength(value, value), 0.0)

    def test_custom_weights_are_normalized(self):
        # trust_weight=1, love_weight=3 -> trust 占 1/4。
        self.assertAlmostEqual(
            relationship_strength(
                1.0, 0.0, trust_weight=1.0, love_weight=3.0,
            ),
            0.25,
        )

    def test_invalid_weights_fall_back_to_defaults(self):
        self.assertAlmostEqual(
            relationship_strength(
                0.5, 1.0, trust_weight=float("nan"), love_weight=0.0,
            ),
            0.5,
        )
        self.assertAlmostEqual(
            relationship_strength(
                0.5, 1.0, trust_weight=0.0, love_weight=0.0,
            ),
            0.5,
        )


class AutoPairTest(unittest.TestCase):
    def test_from_relationship_uses_average_of_both_engines(self):
        source = make_engine(trust=1.0, love=2.0)
        target = make_engine(trust=0.0, love=0.0)
        pair = ContagionPair.from_relationship(source, target)

        self.assertTrue(pair.auto_strength)
        self.assertAlmostEqual(pair.strength, 0.5)

    def test_explicit_strength_is_unchanged(self):
        pair = ContagionPair(make_engine(), make_engine())
        self.assertFalse(pair.auto_strength)
        self.assertEqual(pair.strength, 1.0)

        explicit = ContagionPair(
            make_engine(trust=1.0, love=2.0),
            make_engine(trust=1.0, love=2.0),
            strength=0.3,
        )
        self.assertFalse(explicit.auto_strength)
        self.assertEqual(explicit.strength, 0.3)

    def test_refresh_updates_after_state_change(self):
        source = make_engine(trust=0.25, love=0.2)
        target = make_engine(trust=0.25, love=0.2)
        pair = ContagionPair.from_relationship(source, target)
        self.assertAlmostEqual(pair.strength, 0.19)

        source.state.trust = 1.0
        source.state.love = 2.0
        target.state.trust = 0.0
        target.state.love = 0.0
        self.assertAlmostEqual(pair.refresh_relationship_strength(), 0.5)
        self.assertAlmostEqual(pair.strength, 0.5)

    def test_auto_strength_refreshes_on_derive(self):
        source = make_engine()
        target = make_engine()
        pair = ContagionPair(
            source, target, gain=0.1, cap=0.2, auto_strength=True,
        )
        self.assertAlmostEqual(pair.strength, 0.19)

        source.state.trust = 1.0
        source.state.love = 2.0
        target.state.trust = 1.0
        target.state.love = 2.0

        bias = pair.derive(Appraisal(expectedness=1.0))
        self.assertAlmostEqual(pair.strength, 1.0)
        self.assertAlmostEqual(bias["expectedness"], 0.1)

    def test_auto_strength_zero_disables_contagion(self):
        source = make_engine(trust=0.0, love=0.0)
        target = make_engine(trust=0.0, love=0.0)
        source_control = make_engine(trust=0.0, love=0.0)
        target_control = make_engine(trust=0.0, love=0.0)
        pair = ContagionPair.from_relationship(source, target)

        self.assertEqual(pair.derive(negative_appraisal()), {})
        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW,
        )

        self.assertEqual(
            source_result,
            source_control.tick(negative_appraisal(), now=NOW),
        )
        self.assertEqual(
            target_result,
            target_control.tick(positive_appraisal(), now=NOW),
        )

    def test_auto_strength_respects_cap_and_bidirectional(self):
        source = make_engine(trust=1.0, love=2.0)
        target = make_engine(trust=1.0, love=2.0)
        pair = ContagionPair.from_relationship(
            source, target, gain=1.0, cap=0.05,
        )
        self.assertAlmostEqual(pair.strength, 1.0)

        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW,
        )
        self.assertIn("contagion_bias", source_result)
        self.assertIn("contagion_bias", target_result)
        for result in (source_result, target_result):
            for value in result["contagion_bias"].values():
                self.assertLessEqual(abs(value), 0.05 + 1e-12)

    def test_auto_strength_pair_still_rejects_shared_state(self):
        shared = make_engine()
        with self.assertRaises(ValueError):
            ContagionPair.from_relationship(shared, shared)

    def test_default_relationship_weights_are_documented(self):
        self.assertAlmostEqual(
            DEFAULT_RELATIONSHIP_TRUST_WEIGHT
            + DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
