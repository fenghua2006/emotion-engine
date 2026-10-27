"""L6 JEV：文本 → 六维 Appraisal、置信度、证据与成本护栏。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core import APPRAISAL_LIMITS
from jev import JEV, NEUTRAL_CONFIDENCE, analyze, make_appraisal


class RuleScoringTest(unittest.TestCase):
    def test_positive_text_is_relevant_and_conducive(self):
        result = analyze("我好喜欢你，谢谢你陪我")
        self.assertEqual(result.source, "rules")
        self.assertGreater(result.confidence, 0.5)
        self.assertGreater(result.appraisal.goal_relevance, 0.5)
        self.assertGreater(result.appraisal.goal_conduciveness, 0.0)
        self.assertGreater(result.appraisal.social_evaluation, 0.0)
        self.assertTrue(result.evidence)

    def test_negative_text_is_obstructive(self):
        result = analyze("我好难过，你骗了我")
        self.assertEqual(result.source, "rules")
        self.assertGreater(result.appraisal.goal_relevance, 0.5)
        self.assertLess(result.appraisal.goal_conduciveness, 0.0)
        self.assertLess(result.appraisal.social_evaluation, 0.0)

    def test_insult_is_high_relevance_and_negative_evaluation(self):
        result = analyze("你真蠢，滚开")
        self.assertEqual(result.source, "rules")
        self.assertGreaterEqual(result.appraisal.goal_relevance, 0.7)
        self.assertLessEqual(result.appraisal.social_evaluation, -0.5)

    def test_unexpected_and_routine_shift_expectedness(self):
        unexpected = analyze("没想到你居然离开了")
        routine = analyze("你又迟到了，每次都这样")
        self.assertLess(unexpected.appraisal.expectedness, 0.5)
        self.assertGreater(routine.appraisal.expectedness, 0.5)

    def test_agency_cues_shift_coping_and_attribution(self):
        self_agency = analyze("我可以解决，我来处理")
        helpless = analyze("我没办法，只能这样")
        other_agency = analyze("都怪你，你让我很难受")
        self.assertGreater(self_agency.appraisal.coping_potential, 0.5)
        self.assertLess(helpless.appraisal.coping_potential, 0.5)
        self.assertGreater(other_agency.appraisal.other_agency, 0.5)

    def test_negated_insult_is_not_counted(self):
        result = analyze("我不讨厌你")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(result.confidence, NEUTRAL_CONFIDENCE)
        self.assertNotIn("insult", " ".join(result.evidence))

    def test_negated_positive_becomes_negative(self):
        result = analyze("我不喜欢你")
        self.assertLess(result.appraisal.goal_conduciveness, 0.0)

    def test_neutral_text_falls_back_with_low_confidence(self):
        result = analyze("今天我们就随便聊聊")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(result.confidence, NEUTRAL_CONFIDENCE)

    def test_all_dimensions_stay_in_bounds(self):
        samples = [
            "我爱你，谢谢你，你真好",
            "我恨你，你真恶心，去死",
            "没想到你居然骗我，我没办法",
            "",
        ]
        for text in samples:
            appraisal = analyze(text).appraisal
            for name, (lo, hi) in APPRAISAL_LIMITS.items():
                value = getattr(appraisal, name)
                self.assertGreaterEqual(value, lo, f"{name} < {lo} for {text!r}")
                self.assertLessEqual(value, hi, f"{name} > {hi} for {text!r}")

    def test_empty_text_has_zero_confidence(self):
        result = analyze("   ")
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.source, "fallback")

    def test_make_appraisal_clamps_and_defaults(self):
        appraisal = make_appraisal({
            "goal_relevance": 3,
            "goal_conduciveness": -9,
            "expectedness": "bad",
        })
        self.assertEqual(appraisal.goal_relevance, 1.0)
        self.assertEqual(appraisal.goal_conduciveness, -1.0)
        self.assertEqual(appraisal.expectedness, 0.5)


class CacheAndBatchTest(unittest.TestCase):
    def test_cache_hit_marks_result_and_counts(self):
        engine = JEV()
        first = engine.analyze("我好喜欢你")
        second = engine.analyze("我好喜欢你")
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(engine.cache_stats()["hits"], 1)
        self.assertEqual(engine.cache_stats()["misses"], 1)

    def test_cache_eviction_respects_capacity(self):
        engine = JEV(cache_size=1)
        engine.analyze("我好喜欢你")
        engine.analyze("我好难过")
        self.assertEqual(engine.cache_stats()["size"], 1)
        engine.analyze("我好喜欢你")
        self.assertEqual(engine.cache_stats()["misses"], 3)

    def test_reset_cache_clears_stats(self):
        engine = JEV()
        engine.analyze("我好喜欢你")
        engine.reset_cache()
        self.assertEqual(engine.cache_stats(), {
            "size": 0, "capacity": 256, "hits": 0, "misses": 0,
        })

    def test_batch_preserves_order_and_chunks(self):
        engine = JEV(max_batch=2)
        texts = ["我好喜欢你", "我好难过", "没想到", "今天聊聊", "谢谢你"]
        results = engine.analyze_many(texts)
        self.assertEqual(len(results), len(texts))
        self.assertEqual(
            [result.appraisal.goal_conduciveness for result in results],
            [engine.analyze(text).appraisal.goal_conduciveness for text in texts],
        )

    def test_llm_flag_is_part_of_cache_key(self):
        engine = JEV(use_llm=False)
        with patch("jev.call_llm", return_value=(None, None)) as mocked:
            engine.analyze("今天聊聊")
            engine.analyze("今天聊聊", use_llm=True)
        self.assertEqual(mocked.call_count, 1)


class LLMFallbackTest(unittest.TestCase):
    def test_llm_failure_keeps_rules_or_fallback(self):
        engine = JEV(use_llm=True)
        with patch("jev.call_llm", return_value=(None, None)):
            result = engine.analyze("今天聊聊")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(result.confidence, NEUTRAL_CONFIDENCE)

    def test_llm_json_is_parsed_and_clamped(self):
        payload = (
            '{"appraisal": {"goal_relevance": 0.9, "goal_conduciveness": 9,'
            ' "expectedness": -5, "coping_potential": 0.7, "other_agency": 0.6,'
            ' "social_evaluation": -2}, "confidence": 0.83,'
            ' "evidence": ["llm:test"]}'
        )
        engine = JEV(use_llm=True)
        with patch("jev.call_llm", return_value=(payload, "deepseek")):
            result = engine.analyze("今天聊聊")
        self.assertEqual(result.source, "llm")
        self.assertEqual(result.confidence, 0.83)
        self.assertEqual(result.appraisal.goal_conduciveness, 1.0)
        self.assertEqual(result.appraisal.expectedness, 0.0)
        self.assertEqual(result.appraisal.social_evaluation, -1.0)
        self.assertEqual(result.evidence, ("llm:test",))

    def test_malformed_llm_json_falls_back(self):
        engine = JEV(use_llm=True)
        with patch("jev.call_llm", return_value=("not json", "deepseek")):
            result = engine.analyze("今天聊聊")
        self.assertEqual(result.source, "fallback")

    def test_llm_skipped_when_rules_are_confident(self):
        engine = JEV(use_llm=True)
        with patch("jev.call_llm", return_value=(None, None)) as mocked:
            result = engine.analyze("我好喜欢你，谢谢你陪我")
        self.assertEqual(result.source, "rules")
        self.assertEqual(mocked.call_count, 0)

    def test_force_llm_overrides_confidence_gate(self):
        payload = '{"appraisal": {"goal_relevance": 0.4}, "confidence": 0.9}'
        engine = JEV(use_llm=True)
        with patch("jev.call_llm", return_value=(payload, "deepseek")) as mocked:
            result = engine.analyze("我好喜欢你，谢谢你陪我", force_llm=True)
        self.assertEqual(result.source, "llm")
        self.assertEqual(mocked.call_count, 1)

    def test_llm_options_are_forwarded(self):
        engine = JEV(
            use_llm=True,
            timeout=3.5,
            llm_options={"max_tokens": 99, "api_key": "test-key"},
        )
        with patch("jev.call_llm", return_value=(None, None)) as mocked:
            engine.analyze("今天聊聊")
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 3.5)
        self.assertEqual(kwargs["max_tokens"], 99)
        self.assertEqual(kwargs["api_key"], "test-key")


if __name__ == "__main__":
    unittest.main()
