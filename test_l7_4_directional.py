"""L7.4：方向性关系模型测试（A→B 与 B→A 各自独立）。"""

import unittest

from contagion import (
    ContagionEdge,
    ContagionNetwork,
    ContagionPair,
    DirectionalRelationship,
    directional_relationship_strength,
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


def rounded(bias):
    return {key: round(value, 4) for key, value in bias.items()}


class DirectionalStrengthTest(unittest.TestCase):
    def test_none_or_zero_relationship(self):
        self.assertEqual(directional_relationship_strength(None), 0.0)
        self.assertEqual(
            directional_relationship_strength(
                DirectionalRelationship(),
            ),
            0.0,
        )

    def test_formula_normalizes_three_components(self):
        relationship = DirectionalRelationship(
            trust=0.5, love=1.0, familiarity=0.5,
        )
        # 0.6 / 0.4 / 0.2 归一化后，三项各取 0.5 → 0.5。
        self.assertAlmostEqual(
            directional_relationship_strength(relationship), 0.5,
        )

    def test_full_relationship_is_one(self):
        self.assertAlmostEqual(
            directional_relationship_strength(
                DirectionalRelationship(
                    trust=1.0, love=2.0, familiarity=1.0,
                ),
            ),
            1.0,
        )

    def test_boundaries_reduce_strength(self):
        relationship = DirectionalRelationship(
            trust=1.0, love=2.0, familiarity=1.0,
            boundaries=("不谈前任", "不深夜打扰"),
        )
        self.assertAlmostEqual(
            directional_relationship_strength(relationship), 1.0 / 1.5,
        )
        self.assertAlmostEqual(
            directional_relationship_strength(
                relationship, boundary_penalty=0.5,
            ),
            0.5,
        )

    def test_empty_boundaries_do_not_penalize(self):
        relationship = DirectionalRelationship(
            trust=1.0, love=2.0, familiarity=1.0,
            boundaries=("", "  "),
        )
        self.assertAlmostEqual(
            directional_relationship_strength(relationship), 1.0,
        )
        self.assertAlmostEqual(
            directional_relationship_strength(
                DirectionalRelationship(
                    trust=1.0, love=2.0, familiarity=1.0,
                    boundaries="single boundary",
                ),
            ),
            0.8,
        )

    def test_values_are_clamped(self):
        self.assertAlmostEqual(
            directional_relationship_strength({
                "trust": 2.0, "love": 4.0, "familiarity": 5.0,
            }),
            1.0,
        )
        self.assertAlmostEqual(
            directional_relationship_strength({
                "trust": -1.0, "love": -2.0, "familiarity": -3.0,
            }),
            0.0,
        )

    def test_invalid_values_are_zero(self):
        for value in (None, float("nan"), float("inf"), True, "0.5"):
            with self.subTest(value=value):
                self.assertEqual(
                    directional_relationship_strength({
                        "trust": value,
                        "love": value,
                        "familiarity": value,
                    }),
                    0.0,
                )

    def test_custom_weights_are_normalized(self):
        self.assertAlmostEqual(
            directional_relationship_strength(
                {"trust": 1.0, "love": 0.0, "familiarity": 0.0},
                trust_weight=1.0,
                love_weight=1.0,
                familiarity_weight=2.0,
            ),
            0.25,
        )

    def test_invalid_or_zero_weights_fall_back(self):
        self.assertAlmostEqual(
            directional_relationship_strength(
                {"trust": 1.0, "love": 0.0, "familiarity": 0.0},
                trust_weight=float("nan"),
                love_weight=0.0,
                familiarity_weight=0.0,
            ),
            1.0,
        )
        self.assertAlmostEqual(
            directional_relationship_strength(
                {"trust": 0.5, "love": 1.0, "familiarity": 0.5},
                trust_weight=0.0,
                love_weight=0.0,
                familiarity_weight=0.0,
            ),
            0.5,
        )

    def test_mapping_relationship_is_supported(self):
        self.assertAlmostEqual(
            directional_relationship_strength({
                "trust": 0.5,
                "love": 1.0,
                "familiarity": 0.5,
            }),
            0.5,
        )


class DirectionalPairTest(unittest.TestCase):
    def test_directions_are_independent(self):
        source = make_engine(trust=1.0, love=2.0)
        target = make_engine(trust=0.0, love=0.0)
        pair = ContagionPair.from_directional_relationship(source, target)

        self.assertTrue(pair.directional)
        self.assertAlmostEqual(
            pair.source_to_target_strength, 5.0 / 6.0,
        )
        self.assertAlmostEqual(pair.target_to_source_strength, 0.0)
        self.assertAlmostEqual(pair.strength, 5.0 / 12.0)

        self.assertIn("goal_conduciveness", pair.derive(negative_appraisal()))
        self.assertEqual(pair.derive_reverse(positive_appraisal()), {})

    def test_bidirectional_uses_each_direction(self):
        source = make_engine(trust=1.0, love=2.0)
        target = make_engine(trust=0.0, love=0.0)
        pair = ContagionPair.from_directional_relationship(source, target)

        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW,
        )
        # target -> source 强度为 0，source 不被感染。
        self.assertNotIn("contagion_bias", source_result)
        self.assertIn("contagion_bias", target_result)

    def test_manual_bidirectional_biases_match(self):
        source = make_engine(trust=0.8, love=1.2)
        target = make_engine(trust=0.5, love=0.6)
        pair = ContagionPair.from_directional_relationship(source, target)

        source_bias = pair.derive_reverse(positive_appraisal())
        target_bias = pair.derive(negative_appraisal())
        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW,
        )
        self.assertEqual(
            source_result["contagion_bias"], rounded(source_bias),
        )
        self.assertEqual(
            target_result["contagion_bias"], rounded(target_bias),
        )

    def test_tick_pair_uses_source_to_target(self):
        source = make_engine(trust=0.8, love=1.2)
        target = make_engine(trust=0.0, love=0.0)
        pair = ContagionPair.from_directional_relationship(source, target)

        expected = pair.derive(negative_appraisal())
        source_result, target_result = pair.tick_pair(
            negative_appraisal(), positive_appraisal(), now=NOW,
        )
        self.assertNotIn("contagion_bias", source_result)
        self.assertEqual(
            target_result["contagion_bias"], rounded(expected),
        )

    def test_explicit_relationship_overrides_engine_state(self):
        source = make_engine(trust=0.0, love=0.0)
        target = make_engine(trust=0.0, love=0.0)
        pair = ContagionPair.from_directional_relationship(
            source,
            target,
            source_relationship=DirectionalRelationship(
                trust=1.0, love=2.0,
            ),
        )
        self.assertAlmostEqual(
            pair.source_to_target_strength, 5.0 / 6.0,
        )
        self.assertAlmostEqual(pair.target_to_source_strength, 0.0)

    def test_refresh_after_relationship_change(self):
        source = make_engine(trust=0.25, love=0.2)
        target = make_engine(trust=0.25, love=0.2)
        pair = ContagionPair.from_directional_relationship(source, target)
        initial = 0.25 / 2.0 + (0.2 / 2.0) / 3.0
        self.assertAlmostEqual(
            pair.source_to_target_strength, initial,
        )
        self.assertAlmostEqual(
            pair.target_to_source_strength, initial,
        )

        source.state.trust = 1.0
        source.state.love = 2.0
        pair.refresh_relationship_strength()
        self.assertAlmostEqual(
            pair.source_to_target_strength, 5.0 / 6.0,
        )
        self.assertAlmostEqual(
            pair.target_to_source_strength, initial,
        )
        self.assertAlmostEqual(
            pair.strength, (5.0 / 6.0 + initial) / 2.0,
        )

    def test_directional_and_auto_strength_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            ContagionPair(
                make_engine(), make_engine(),
                directional=True,
                auto_strength=True,
            )

    def test_directional_pair_rejects_shared_state(self):
        engine = make_engine()
        with self.assertRaises(ValueError):
            ContagionPair.from_directional_relationship(engine, engine)

    def test_relationship_is_not_mutated(self):
        relationship = DirectionalRelationship(
            trust=0.8,
            love=1.2,
            familiarity=0.6,
            boundaries=("不深夜打扰",),
        )
        pair = ContagionPair.from_directional_relationship(
            make_engine(trust=0.0, love=0.0),
            make_engine(trust=0.0, love=0.0),
            source_relationship=relationship,
        )
        pair.derive(negative_appraisal())
        self.assertEqual(
            relationship,
            DirectionalRelationship(
                trust=0.8,
                love=1.2,
                familiarity=0.6,
                boundaries=("不深夜打扰",),
            ),
        )

    def test_directional_strength_respects_cap(self):
        pair = ContagionPair.from_directional_relationship(
            make_engine(trust=1.0, love=2.0),
            make_engine(trust=1.0, love=2.0),
            gain=1.0,
            cap=0.05,
        )
        bias = pair.derive(negative_appraisal())
        for value in bias.values():
            self.assertLessEqual(abs(value), 0.05 + 1e-12)

    def test_network_can_use_directional_edge_strength(self):
        relationship = DirectionalRelationship(
            trust=1.0, love=2.0, familiarity=1.0,
        )
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine(), "C": make_engine()},
            [
                ContagionEdge(
                    "A", "B",
                    directional_relationship_strength(relationship),
                ),
                ContagionEdge("B", "C", 1.0),
            ],
            hop_decay=0.5,
        )
        bias = network.derive({"A": negative_appraisal()})
        self.assertAlmostEqual(
            bias["B"]["goal_conduciveness"], -0.08,
        )
        self.assertAlmostEqual(
            bias["C"]["goal_conduciveness"], -0.04,
        )


if __name__ == "__main__":
    unittest.main()
