"""L7.1：双向感染与关系强度测试。"""

import unittest

from contagion import (
    DEFAULT_CONTAGION_GAIN,
    DEFAULT_CONTAGION_STRENGTH,
    ContagionPair,
)
from core import Appraisal, EmotionalState, Personality
from engine import EmotionEngine, MemoryStore, SensitizationStore


NOW = 1_800_000_000.0

CONTAGION_KEYS = {
    "contagion_enabled",
    "contagion_applied",
    "appraisal_before_contagion",
}


def make_engine(trust: float = 0.25) -> EmotionEngine:
    personality = Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    state.trust = trust
    state._last_update = NOW
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
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


def negative_appraisal() -> Appraisal:
    return Appraisal(
        goal_relevance=0.8,
        goal_conduciveness=-0.6,
        expectedness=0.2,
        coping_potential=0.2,
        other_agency=0.6,
        social_evaluation=-0.4,
    )


def neutral_appraisal() -> Appraisal:
    # goal_relevance 不参与感染；四个传输维度都在中性值。
    return Appraisal(goal_relevance=0.8)


class StrengthTest(unittest.TestCase):
    def test_default_strength_and_effective_gain(self):
        pair = ContagionPair(make_engine(), make_engine())
        self.assertEqual(pair.strength, DEFAULT_CONTAGION_STRENGTH)
        self.assertAlmostEqual(pair.effective_gain, DEFAULT_CONTAGION_GAIN)

    def test_strength_scales_bias_without_hitting_cap(self):
        appraisal = Appraisal(expectedness=1.0)
        full = ContagionPair(
            make_engine(), make_engine(),
            gain=0.1, cap=0.2, strength=1.0,
        )
        half = ContagionPair(
            make_engine(), make_engine(),
            gain=0.1, cap=0.2, strength=0.5,
        )
        self.assertAlmostEqual(
            full.derive(appraisal)["expectedness"], 0.1,
        )
        self.assertAlmostEqual(
            half.derive(appraisal)["expectedness"], 0.05,
        )

    def test_strength_zero_disables_contagion(self):
        source = make_engine()
        target = make_engine()
        source_control = make_engine()
        target_control = make_engine()
        pair = ContagionPair(source, target, strength=0.0)

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
        self.assertNotIn("contagion_bias", source_result)
        self.assertNotIn("contagion_bias", target_result)

    def test_invalid_strength_is_normalized(self):
        cases = [
            (2.0, 1.0),
            (-1.0, 0.0),
            (float("nan"), 1.0),
            (float("inf"), 1.0),
            (None, 1.0),
            (True, 1.0),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                pair = ContagionPair(
                    make_engine(), make_engine(), strength=value,
                )
                self.assertAlmostEqual(pair.strength, expected)

    def test_strength_does_not_bypass_cap(self):
        pair = ContagionPair(
            make_engine(), make_engine(),
            gain=1.0, cap=0.05, strength=1.0,
        )
        bias = pair.derive(Appraisal(
            goal_conduciveness=1.0,
            expectedness=0.0,
            coping_potential=0.0,
            social_evaluation=-1.0,
        ))
        self.assertEqual(len(bias), 4)
        for value in bias.values():
            self.assertLessEqual(abs(value), 0.05 + 1e-12)


class BidirectionalTest(unittest.TestCase):
    def test_bidirectional_applies_both_directions(self):
        source = make_engine()
        target = make_engine()
        source_control = make_engine()
        target_control = make_engine()
        pair = ContagionPair(source, target)

        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW,
        )
        source_plain = source_control.tick(negative_appraisal(), now=NOW)
        target_plain = target_control.tick(positive_appraisal(), now=NOW)

        self.assertIn("contagion_bias", source_result)
        self.assertIn("contagion_bias", target_result)

        # A 收到 B 的正面偏置：负面状态被削弱。
        self.assertLess(
            source_result["state"]["sadness"],
            source_plain["state"]["sadness"],
        )
        self.assertGreater(
            source_result["state"]["joy"],
            source_plain["state"]["joy"],
        )

        # B 收到 A 的负面偏置：负面状态被放大。
        self.assertGreater(
            target_result["state"]["sadness"],
            target_plain["state"]["sadness"],
        )
        self.assertLess(
            target_result["state"]["joy"],
            target_plain["state"]["joy"],
        )

    def test_bidirectional_is_order_independent(self):
        source = make_engine()
        target = make_engine()
        source_manual = make_engine()
        target_manual = make_engine()
        pair = ContagionPair(source, target)

        source_bias = pair.derive(positive_appraisal())
        target_bias = pair.derive(negative_appraisal())

        # 先手算两个偏置，再按相反顺序 tick；结果应与 tick_bidirectional 一致。
        manual_target = target_manual.tick(
            positive_appraisal(), contagion_bias=target_bias, now=NOW,
        )
        manual_source = source_manual.tick(
            negative_appraisal(), contagion_bias=source_bias, now=NOW,
        )

        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW,
        )
        self.assertEqual(source_result, manual_source)
        self.assertEqual(target_result, manual_target)

    def test_mirrored_pairs_produce_mirrored_results(self):
        pair_ab = ContagionPair(make_engine(), make_engine())
        pair_ba = ContagionPair(make_engine(), make_engine())

        a_result, b_result = pair_ab.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW,
        )
        b_mirror, a_mirror = pair_ba.tick_bidirectional(
            positive_appraisal(), negative_appraisal(), now=NOW,
        )

        self.assertEqual(a_result, a_mirror)
        self.assertEqual(b_result, b_mirror)

    def test_one_neutral_side_only_infects_the_other(self):
        source = make_engine()
        target = make_engine()
        source_control = make_engine()
        pair = ContagionPair(source, target)

        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), neutral_appraisal(), now=NOW,
        )

        # A 负面 → B 获得偏置；B 中性 → A 不获得偏置。
        self.assertIn("contagion_bias", target_result)
        self.assertNotIn("contagion_bias", source_result)
        self.assertEqual(
            source_result,
            source_control.tick(negative_appraisal(), now=NOW),
        )

    def test_missing_appraisal_is_a_noop(self):
        cases = [
            (None, positive_appraisal()),
            (negative_appraisal(), None),
            (None, None),
        ]
        for source_appraisal, target_appraisal in cases:
            with self.subTest(
                source=source_appraisal is not None,
                target=target_appraisal is not None,
            ):
                source = make_engine()
                target = make_engine()
                source_control = make_engine()
                target_control = make_engine()
                pair = ContagionPair(source, target)

                source_result, target_result = pair.tick_bidirectional(
                    source_appraisal, target_appraisal, now=NOW,
                )

                self.assertEqual(
                    source_result,
                    source_control.tick(source_appraisal, now=NOW),
                )
                self.assertEqual(
                    target_result,
                    target_control.tick(target_appraisal, now=NOW),
                )
                if source_appraisal is None:
                    self.assertNotIn("contagion_bias", source_result)
                if target_appraisal is None:
                    self.assertNotIn("contagion_bias", target_result)

    def test_bidirectional_trace_adds_three_keys_per_side(self):
        plain_source = make_engine().tick(
            negative_appraisal(), now=NOW, trace=True,
        )
        plain_target = make_engine().tick(
            positive_appraisal(), now=NOW, trace=True,
        )
        pair = ContagionPair(make_engine(), make_engine())
        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW, trace=True,
        )

        for result, plain in (
            (source_result, plain_source),
            (target_result, plain_target),
        ):
            self.assertEqual(
                set(result["trace"]) - set(plain["trace"]),
                CONTAGION_KEYS,
            )
            self.assertEqual(
                set(plain["trace"]) - set(result["trace"]), set(),
            )

    def test_memory_priors_compose_on_both_sides(self):
        pair = ContagionPair(make_engine(), make_engine())
        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW, trace=True,
            source_appraisal_bias={"goal_conduciveness": 0.05},
            target_appraisal_bias={"expectedness": -0.05},
        )

        for result in (source_result, target_result):
            self.assertIn("memory_bias", result)
            self.assertIn("contagion_bias", result)
            self.assertTrue(result["trace"]["memory_bias_enabled"])
            self.assertTrue(result["trace"]["contagion_enabled"])

        self.assertAlmostEqual(
            source_result["trace"]["appraisal_before_contagion"]
            ["goal_conduciveness"],
            negative_appraisal().goal_conduciveness + 0.05,
        )
        self.assertAlmostEqual(
            target_result["trace"]["appraisal_before_contagion"]
            ["expectedness"],
            positive_appraisal().expectedness - 0.05,
        )

    def test_boundary_flags_are_forwarded(self):
        pair = ContagionPair(make_engine(), make_engine())
        source_control = make_engine()
        target_control = make_engine()

        source_result, target_result = pair.tick_bidirectional(
            negative_appraisal(), positive_appraisal(), now=NOW,
            source_boundary_violation=True,
            target_boundary_violation=True,
        )

        source_bias = pair.derive(positive_appraisal())
        target_bias = pair.derive(negative_appraisal())
        self.assertEqual(
            source_result,
            source_control.tick(
                negative_appraisal(),
                boundary_violation=True,
                contagion_bias=source_bias,
                now=NOW,
            ),
        )
        self.assertEqual(
            target_result,
            target_control.tick(
                positive_appraisal(),
                boundary_violation=True,
                contagion_bias=target_bias,
                now=NOW,
            ),
        )

    def test_tick_pair_remains_one_way(self):
        pair = ContagionPair(make_engine(), make_engine())
        source_result, target_result = pair.tick_pair(
            negative_appraisal(), positive_appraisal(), now=NOW,
        )
        self.assertNotIn("contagion_bias", source_result)
        self.assertIn("contagion_bias", target_result)


if __name__ == "__main__":
    unittest.main()
