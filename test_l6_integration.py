"""L6 集成：Noul 边界检测、respond / respond_llm 的可选 JEV 链路。"""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from core import Appraisal
from engine import (
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SensitizationStore,
    respond,
    respond_llm,
)
from jev import make_appraisal
from noul import BoundarySignal, detect, is_violation

NOW = 1_700_000_000.0


def make_engine(trust: float = 0.25,
                now: float = NOW) -> EmotionEngine:
    personality = Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    state.trust = trust
    state._last_update = now
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


class NoulTest(unittest.TestCase):
    def test_empty_text_is_not_violation(self):
        signal = detect("")
        self.assertFalse(signal.violated)
        self.assertEqual(signal.confidence, 0.0)

    def test_intimacy_depends_on_trust(self):
        low = detect("老婆，我回来了", trust=0.2)
        high = detect("老婆，我回来了", trust=0.8)
        self.assertTrue(low.violated)
        self.assertFalse(high.violated)
        self.assertIn("intimacy", low.categories)

    def test_insult_is_violation(self):
        signal = detect("你真蠢，闭嘴")
        self.assertTrue(signal.violated)
        self.assertIn("insult", signal.categories)
        self.assertGreaterEqual(signal.confidence, 0.5)

    def test_coercion_and_threat_are_strong_signals(self):
        signal = detect("如果你不答应，我就毁了你")
        self.assertTrue(signal.violated)
        self.assertIn("coercion", signal.categories)
        self.assertIn("threat", signal.categories)
        self.assertGreaterEqual(signal.confidence, 0.75)

    def test_negated_insult_is_ignored(self):
        self.assertFalse(detect("我不讨厌你").violated)

    def test_threshold_is_configurable(self):
        self.assertFalse(detect("老婆", trust=0.8, threshold=0.9).violated)
        self.assertTrue(detect("跪下", trust=0.8, threshold=0.5).violated)

    def test_is_violation_convenience(self):
        self.assertTrue(is_violation("闭嘴"))
        self.assertFalse(is_violation("今天天气不错"))

    def test_signal_to_dict(self):
        signal = BoundarySignal(True, 0.7, ("insult",), ("insult:蠢",))
        data = signal.to_dict()
        self.assertTrue(data["violated"])
        self.assertEqual(data["categories"], ["insult"])
        self.assertEqual(data["evidence"], ["insult:蠢"])


class RespondIntegrationTest(unittest.TestCase):
    def test_respond_attaches_trace_only_when_requested(self):
        engine = make_engine()
        plain = respond(engine, "hello", appraisal=Appraisal(goal_relevance=0.5))
        self.assertNotIn("trace", plain)
        engine2 = make_engine()
        traced = respond(
            engine2, "hello",
            appraisal=Appraisal(goal_relevance=0.5),
            trace=True,
        )
        self.assertIn("trace", traced)
        self.assertIn("before", traced["trace"])

    def test_respond_passes_boundary_violation_to_tick(self):
        appraisal = Appraisal(
            goal_relevance=0.8, goal_conduciveness=-0.7,
            social_evaluation=-0.5,
        )
        current = time.time()
        plain = respond(
            make_engine(trust=0.2, now=current), "老婆",
            appraisal=appraisal,
        )
        gated = respond(
            make_engine(trust=0.2, now=current), "老婆",
            appraisal=appraisal, boundary_violation=True,
        )
        self.assertNotEqual(plain["_state"], gated["_state"])

    def test_respond_llm_uses_jev_when_requested(self):
        engine = make_engine()
        with patch("engine.call_llm", return_value=(None, None)):
            result = respond_llm(
                engine, "我好喜欢你，谢谢你陪我",
                use_jev=True,
            )
        self.assertFalse(result["_llm_used"])
        self.assertEqual(result["_jev"]["source"], "rules")
        self.assertIn("appraisal", result["_jev"])

        # 同一个 JEV Appraisal 直接 tick，状态应一致。
        expected_engine = make_engine()
        appraisal = make_appraisal(result["_jev"]["appraisal"])
        expected = respond(expected_engine, "x", appraisal=appraisal)
        self.assertEqual(result["_state"], expected["_state"])

    def test_respond_llm_jev_failure_is_non_fatal(self):
        engine = make_engine()
        with patch("jev.analyze", side_effect=RuntimeError("boom")):
            with patch("engine.call_llm", return_value=(None, None)):
                result = respond_llm(
                    engine, "今天我有点累",
                    use_jev=True,
                )
        self.assertIn("_jev_error", result)
        self.assertIn("utterance", result)

    def test_respond_llm_auto_boundary(self):
        engine = make_engine(trust=0.2)
        with patch("engine.call_llm", return_value=(None, None)):
            result = respond_llm(
                engine, "老婆，我回来了",
                auto_boundary=True,
            )
        self.assertTrue(result["_boundary"]["violated"])

    def test_explicit_boundary_overrides_auto(self):
        engine = make_engine(trust=0.2)
        with patch("engine.call_llm", return_value=(None, None)):
            result = respond_llm(
                engine, "老婆，我回来了",
                auto_boundary=True,
                boundary_violation=False,
            )
        self.assertNotIn("_boundary", result)

    def test_llm_does_not_mutate_personality(self):
        engine = make_engine()
        before = engine.personality
        with patch("engine.call_llm", return_value=("答复", "deepseek")):
            result = respond_llm(
                engine, "我好喜欢你",
                use_jev=True,
                api_key="test",
            )
        self.assertTrue(result["_llm_used"])
        self.assertIs(engine.personality, before)
        self.assertEqual(engine.personality, before)


if __name__ == "__main__":
    unittest.main()
