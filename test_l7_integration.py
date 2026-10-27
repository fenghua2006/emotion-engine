"""L7 v0：感染偏置接入 engine.tick / trace / respond 的集成测试。"""

import math
import unittest
from unittest.mock import patch

from contagion import ContagionPair
from core import Appraisal, EmotionalState, Personality, apply_appraisal_bias
from engine import (
    SNAPSHOT_VERSION,
    EmotionEngine,
    MemoryStore,
    SensitizationStore,
    respond,
    respond_llm,
)
from memory import MEMORY_SCHEMA_VERSION


NOW = 1_800_000_000.0

CONTAGION_KEYS = {
    "contagion_enabled",
    "contagion_applied",
    "appraisal_before_contagion",
}


def make_engine(trust=0.25):
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


def positive_appraisal():
    return Appraisal(
        goal_relevance=0.7,
        goal_conduciveness=0.3,
        expectedness=0.6,
        coping_potential=0.6,
        other_agency=0.4,
        social_evaluation=0.2,
    )


def negative_source_appraisal():
    return Appraisal(
        goal_relevance=0.8,
        goal_conduciveness=-0.6,
        expectedness=0.2,
        coping_potential=0.2,
        other_agency=0.6,
        social_evaluation=-0.4,
    )


NEGATIVE_BIAS = {
    "goal_conduciveness": -0.08,
    "expectedness": -0.08,
    "coping_potential": -0.08,
    "social_evaluation": -0.08,
}


class TickContagionTest(unittest.TestCase):
    def test_tick_with_contagion_changes_gated_appraisal(self):
        plain = make_engine().tick(positive_appraisal(), now=NOW)
        biased = make_engine().tick(
            positive_appraisal(), now=NOW, contagion_bias=NEGATIVE_BIAS,
        )
        self.assertNotIn("contagion_bias", plain)
        self.assertIn("contagion_bias", biased)
        self.assertLess(
            biased["gated_appraisal"]["joy"],
            plain["gated_appraisal"]["joy"],
        )
        self.assertGreater(
            biased["gated_appraisal"]["sadness"],
            plain["gated_appraisal"]["sadness"],
        )
        self.assertNotEqual(plain["state"], biased["state"])

    def test_contagion_only_does_not_create_memory_bias(self):
        result = make_engine().tick(
            positive_appraisal(), now=NOW, contagion_bias=NEGATIVE_BIAS,
        )
        self.assertIn("contagion_bias", result)
        self.assertNotIn("memory_bias", result)

    def test_memory_and_contagion_compose(self):
        result = make_engine().tick(
            positive_appraisal(), now=NOW,
            appraisal_bias={"goal_conduciveness": 0.05},
            contagion_bias={"expectedness": -0.08},
            trace=True,
        )
        self.assertIn("memory_bias", result)
        self.assertIn("contagion_bias", result)
        trace = result["trace"]
        self.assertTrue(trace["memory_bias_enabled"])
        self.assertTrue(trace["contagion_enabled"])
        self.assertAlmostEqual(
            trace["appraisal_before_contagion"]["goal_conduciveness"],
            positive_appraisal().goal_conduciveness + 0.05,
        )
        self.assertAlmostEqual(
            trace["appraisal_before_contagion"]["expectedness"],
            positive_appraisal().expectedness,
        )

    def test_trace_applied_delta_matches_appraisal_bias(self):
        result = make_engine().tick(
            positive_appraisal(), now=NOW,
            appraisal_bias={"goal_conduciveness": 0.05},
            contagion_bias=NEGATIVE_BIAS,
            trace=True,
        )
        trace = result["trace"]
        before = trace["appraisal_before_contagion"]
        applied = trace["contagion_applied"]
        expected = apply_appraisal_bias(
            Appraisal(**before), NEGATIVE_BIAS, cap=0.2,
        )
        self.assertEqual(set(applied), set(NEGATIVE_BIAS))
        for dimension, delta in applied.items():
            self.assertAlmostEqual(
                before[dimension] + delta,
                getattr(expected, dimension),
            )

    def test_default_trace_still_has_27_keys(self):
        result = make_engine().tick(positive_appraisal(), now=NOW, trace=True)
        self.assertNotIn("contagion_bias", result)
        self.assertNotIn("memory_bias", result)
        self.assertNotIn("contagion_enabled", result["trace"])
        self.assertEqual(len(result["trace"]), 27)

    def test_contagion_trace_adds_exactly_three_keys(self):
        plain = make_engine().tick(positive_appraisal(), now=NOW, trace=True)
        biased = make_engine().tick(
            positive_appraisal(), now=NOW,
            contagion_bias=NEGATIVE_BIAS, trace=True,
        )
        self.assertEqual(
            set(biased["trace"]) - set(plain["trace"]), CONTAGION_KEYS,
        )
        self.assertEqual(
            set(plain["trace"]) - set(biased["trace"]), set(),
        )

    def test_only_four_dimensions_are_affected(self):
        appraisal = positive_appraisal()
        result = make_engine().tick(
            appraisal, now=NOW, contagion_bias=NEGATIVE_BIAS, trace=True,
        )
        trace = result["trace"]
        self.assertEqual(set(trace["contagion_applied"]), set(NEGATIVE_BIAS))
        self.assertEqual(
            trace["appraisal_before_contagion"]["goal_relevance"],
            appraisal.goal_relevance,
        )
        self.assertEqual(
            trace["appraisal_before_contagion"]["other_agency"],
            appraisal.other_agency,
        )

    def test_invalid_input_is_sanitized(self):
        result = make_engine().tick(
            positive_appraisal(), now=NOW,
            contagion_bias={
                "goal_conduciveness": float("nan"),
                "expectedness": 99.0,
                "coping_potential": True,
                "social_evaluation": "0.5",
                "goal_relevance": 1.0,
                "unknown": 1.0,
            },
            trace=True,
        )
        self.assertEqual(set(result["contagion_bias"]), {"expectedness"})
        self.assertAlmostEqual(result["contagion_bias"]["expectedness"], 0.2)
        for value in result["state"].values():
            self.assertTrue(math.isfinite(value))

    def test_contagion_without_appraisal_is_a_noop(self):
        biased = make_engine().tick(
            None, now=NOW, contagion_bias=NEGATIVE_BIAS, trace=True,
        )
        plain = make_engine().tick(None, now=NOW, trace=True)
        self.assertEqual(biased, plain)
        self.assertNotIn("contagion_bias", biased)
        self.assertNotIn("contagion_enabled", biased["trace"])


class RespondContagionTest(unittest.TestCase):
    def test_respond_forwards_contagion_bias(self):
        engine = make_engine()
        control = make_engine()
        with patch("engine.time.time", return_value=NOW):
            response = respond(
                engine, "hello",
                appraisal=positive_appraisal(),
                contagion_bias=NEGATIVE_BIAS,
                trace=True,
            )
        direct = control.tick(
            positive_appraisal(), now=NOW,
            contagion_bias=NEGATIVE_BIAS, trace=True,
        )
        self.assertEqual(response["_state"], direct["state"])
        self.assertEqual(response["contagion_bias"], direct["contagion_bias"])
        self.assertIn("contagion_enabled", response["trace"])

    def test_respond_llm_forwards_contagion_bias_on_local_fallback(self):
        engine = make_engine()
        control = make_engine()
        with patch("engine.time.time", return_value=NOW):
            with patch("engine.call_llm", return_value=(None, None)):
                result = respond_llm(
                    engine, "hello",
                    appraisal=positive_appraisal(),
                    contagion_bias=NEGATIVE_BIAS,
                )
            expected = respond(
                control, "hello",
                appraisal=positive_appraisal(),
                contagion_bias=NEGATIVE_BIAS,
            )
        self.assertFalse(result["_llm_used"])
        self.assertIn("contagion_bias", result)
        self.assertEqual(result["_state"], expected["_state"])


class ContagionPairIntegrationTest(unittest.TestCase):
    def test_tick_pair_moves_target_but_not_source(self):
        source = make_engine()
        target = make_engine()
        control = make_engine()
        source_control = make_engine()
        pair = ContagionPair(source, target)

        source_result, target_result = pair.tick_pair(
            negative_source_appraisal(),
            positive_appraisal(),
            now=NOW,
        )
        control_result = control.tick(positive_appraisal(), now=NOW)
        source_plain = source_control.tick(
            negative_source_appraisal(), now=NOW,
        )

        self.assertIn("contagion_bias", target_result)
        self.assertNotIn("contagion_bias", source_result)
        self.assertGreater(
            target_result["state"]["sadness"],
            control_result["state"]["sadness"],
        )
        self.assertLess(
            target_result["state"]["joy"],
            control_result["state"]["joy"],
        )
        self.assertEqual(source_result, source_plain)

    def test_schema_versions_and_memory_isolation_unchanged(self):
        self.assertEqual(SNAPSHOT_VERSION, 2)
        self.assertEqual(MEMORY_SCHEMA_VERSION, 1)

        source = make_engine()
        target = make_engine()
        control = make_engine()
        self.assertIsNot(source.memory, target.memory)
        self.assertIsNot(source.scars, target.scars)
        self.assertIsNot(source.tolerance, target.tolerance)

        pair = ContagionPair(source, target)
        _, target_result = pair.tick_pair(
            negative_source_appraisal(),
            positive_appraisal(),
            now=NOW,
        )
        control.tick(positive_appraisal(), now=NOW)

        self.assertIn("contagion_bias", target_result)
        # 普通 tick 自身会写旧版 MemoryStore；感染不额外写记忆。
        self.assertEqual(target.memory.stats(), control.memory.stats())


if __name__ == "__main__":
    unittest.main()
