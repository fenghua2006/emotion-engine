"""L3.5 元情绪 v0 测试。

覆盖：
- 关闭/开启时 trace 与基础曲线兼容性
- 激活阈值、四维元评估、kind 有界
- 中性 profile 与角色 profile 的差异
- 表达/调节/耐受偏置边界
- JSON 序列化、连续事件安全、快照、模块边界
"""

import ast
import json
import math
import os
import pathlib
import tempfile
import unittest

import meta_emotion
from core import (
    Appraisal,
    Channel,
    EmotionalState,
    Personality,
    trust_target,
)
from engine import (
    EmotionEngine,
    MemoryStore,
    SensitizationStore,
    load_snapshot,
    save_snapshot,
)
from meta_emotion import (
    ACTION_BIAS_BONUS,
    ACTION_TO_DISPLAY,
    DISPLAY_RULE_ORDER,
    META_ACTION_ORDER,
    META_DIMENSIONS,
    META_KIND_THRESHOLD,
    META_TARGET_CHANNELS,
    NEUTRAL_META_PROFILE,
    MetaEmotion,
    MetaEmotionProfile,
    _choose_action,
    _profile_weight,
    meta_appraise,
    meta_regulate,
)


TRACE_KEYS = {
    "elapsed_minutes", "before", "after_decay", "after_slow_channels",
    "raw_appraisal", "gated_appraisal", "after_appraisal",
    "after_atmosphere", "after_interaction", "final", "blends",
    "shock_channels", "atmosphere", "tolerance_zone", "slow_positive_input",
    "trust_target", "atmosphere_mode", "atmosphere_before",
    "atmosphere_applied", "atmosphere_after", "atmosphere_shock_density",
    "atmosphere_arousal", "atmosphere_multipliers", "scar_shift",
    "scar_channels", "gated_written", "notes",
}

META_KEYS = {
    "meta_enabled", "meta_appraisal", "meta_emotions", "regulation_bias",
}


def make_engine(*, meta_emotion_enabled=False, meta_emotion_profile=None,
                personality=None):
    personality = personality or Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
        meta_emotion_enabled=meta_emotion_enabled,
        meta_emotion_profile=meta_emotion_profile,
    )


def state_at_baseline(personality=None):
    personality = personality or Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    return state


def severe_event():
    return Appraisal(
        goal_relevance=0.9,
        goal_conduciveness=-0.75,
        expectedness=0.4,
        other_agency=0.9,
        coping_potential=0.55,
        social_evaluation=-0.35,
    )


def positive_event():
    return Appraisal(
        goal_relevance=0.8,
        goal_conduciveness=0.7,
        expectedness=0.6,
        other_agency=0.7,
        coping_potential=0.8,
        social_evaluation=0.6,
    )


def run_tick(engine, appraisal=None, *, trace=True, now=1060.0):
    return engine.tick(appraisal, now=now, trace=trace)


def anger_meta(trace):
    return [
        emotion for emotion in trace["meta_emotions"]
        if emotion["target"] == "anger"
    ]


def assert_meta_finite(testcase, trace):
    for appraisal in trace["meta_appraisal"].values():
        for value in appraisal.values():
            testcase.assertTrue(math.isfinite(value))
            testcase.assertGreaterEqual(value, 0.0)
            testcase.assertLessEqual(value, 1.0)
    for emotion in trace["meta_emotions"]:
        testcase.assertTrue(math.isfinite(emotion["intensity"]))
        testcase.assertGreaterEqual(emotion["intensity"], 0.0)
        testcase.assertLessEqual(emotion["intensity"], 1.0)
        testcase.assertGreaterEqual(emotion["acceptance"], 0.0)
        testcase.assertLessEqual(emotion["acceptance"], 1.0)
    bias = trace["regulation_bias"]
    for gain in bias["expression_gain"].values():
        testcase.assertGreaterEqual(gain, 0.5)
        testcase.assertLessEqual(gain, 1.5)
    testcase.assertGreaterEqual(bias["tolerance_load_bias"], 0.0)
    testcase.assertLessEqual(bias["tolerance_load_bias"], 0.2)
    for rule in bias["display_rule"].values():
        testcase.assertIn(rule, DISPLAY_RULE_ORDER)
    for target in bias["reappraisal_target"]:
        testcase.assertIn(target, META_DIMENSIONS)


class MetaCompatibilityTests(unittest.TestCase):
    def test_meta_disabled_trace_key_set_unchanged(self):
        engine = make_engine()
        engine.state._last_update = 1000.0
        trace = run_tick(engine, severe_event())["trace"]
        self.assertEqual(set(trace), TRACE_KEYS)

    def test_meta_enabled_adds_only_four_trace_keys(self):
        engine = make_engine(meta_emotion_enabled=True)
        engine.state._last_update = 1000.0
        trace = run_tick(engine, severe_event())["trace"]
        self.assertEqual(set(trace), TRACE_KEYS | META_KEYS)
        self.assertTrue(trace["meta_enabled"])
        self.assertIn("anger", trace["meta_appraisal"])

    def test_meta_enabled_trace_false_is_bit_identical(self):
        plain = make_engine()
        meta = make_engine(meta_emotion_enabled=True)
        plain.state._last_update = 1000.0
        meta.state._last_update = 1000.0
        plain_result = run_tick(plain, severe_event(), trace=False)
        meta_result = run_tick(meta, severe_event(), trace=False)
        self.assertEqual(plain_result, meta_result)

    def test_meta_enabled_does_not_change_base_trajectory(self):
        plain = make_engine()
        meta = make_engine(meta_emotion_enabled=True)
        plain.state._last_update = 1000.0
        meta.state._last_update = 1000.0
        plain_result = run_tick(plain, severe_event())
        meta_result = run_tick(meta, severe_event())
        plain_result.pop("trace")
        meta_result.pop("trace")
        self.assertEqual(plain_result, meta_result)
        self.assertEqual(plain.personality, meta.personality)

    def test_profile_alone_does_not_enable_meta(self):
        profile = MetaEmotionProfile(
            kind_weights={"anger": {"shame": 2.0}},
            action_bias={"anger": "suppress"},
        )
        disabled = make_engine(
            meta_emotion_enabled=False, meta_emotion_profile=profile,
        )
        disabled.state._last_update = 1000.0
        trace = run_tick(disabled, severe_event())["trace"]
        self.assertNotIn("meta_enabled", trace)

        enabled = make_engine(
            meta_emotion_enabled=True, meta_emotion_profile=profile,
        )
        enabled.state._last_update = 1000.0
        trace = run_tick(enabled, severe_event())["trace"]
        self.assertIn("meta_enabled", trace)


class MetaActivationTests(unittest.TestCase):
    def test_idle_baseline_has_no_active_targets(self):
        engine = make_engine(meta_emotion_enabled=True)
        engine.state._last_update = 1000.0
        trace = run_tick(engine, None)["trace"]
        self.assertEqual(trace["meta_appraisal"], {})
        self.assertEqual(trace["meta_emotions"], [])
        self.assertEqual(trace["regulation_bias"]["expression_gain"], {})

    def test_activation_requires_felt_and_raw_thresholds(self):
        personality = Personality()
        baseline = personality.baseline()
        state = state_at_baseline(personality)
        reference = baseline[Channel.ANGER]

        state.anger = reference + 0.03
        appraisals, _ = meta_appraise(
            state=state, baseline=baseline, personality=personality,
        )
        self.assertNotIn("anger", appraisals)

        state.anger = reference + 0.07
        appraisals, _ = meta_appraise(
            state=state, baseline=baseline, personality=personality,
        )
        self.assertIn("anger", appraisals)

    def test_activation_is_felt_compressed_across_baselines(self):
        personality = Personality()
        base = personality.baseline()

        low = dict(base)
        low[Channel.ANGER] = 0.05
        high = dict(base)
        high[Channel.ANGER] = 0.50

        low_state = state_at_baseline(personality)
        low_state.anger = 0.17
        high_state = state_at_baseline(personality)
        high_state.anger = 0.62

        low_appraisals, _ = meta_appraise(
            state=low_state, baseline=low, personality=personality,
        )
        high_appraisals, _ = meta_appraise(
            state=high_state, baseline=high, personality=personality,
        )
        low_activation = low_appraisals["anger"].activation
        high_activation = high_appraisals["anger"].activation
        self.assertGreater(low_activation, high_activation)
        self.assertGreater(low_activation, 0.0)
        self.assertLessEqual(low_activation, 1.0)

    def test_trust_reference_uses_trust_target(self):
        personality = Personality()
        baseline = personality.baseline()
        state = state_at_baseline(personality)
        state.love = 0.9
        state.trust = 0.3
        self.assertLess(state.trust, trust_target(state.love))

        appraisals, _ = meta_appraise(
            state=state, baseline=baseline, personality=personality,
        )
        self.assertNotIn("trust", appraisals)

        state.trust = 1.0
        appraisals, _ = meta_appraise(
            state=state, baseline=baseline, personality=personality,
        )
        self.assertIn("trust", appraisals)


class MetaMathTests(unittest.TestCase):
    def test_meta_appraisal_dimensions_are_bounded(self):
        personality = Personality()
        baseline = personality.baseline()
        state = state_at_baseline(personality)
        for channel in Channel:
            setattr(state, channel.value, 5.0)
        appraisals, emotions = meta_appraise(
            state=state,
            baseline=baseline,
            personality=personality,
            appraisal=Appraisal(
                goal_relevance=1.0,
                goal_conduciveness=-1.0,
                expectedness=0.0,
                other_agency=1.0,
                coping_potential=1.0,
                social_evaluation=-1.0,
            ),
            atmosphere=10.0,
            tolerance_zone="hyper",
            overload_ratio=2.0,
            scar_pressure=2.0,
            scar_channels=[channel.value for channel in Channel],
            blends=["a", "b", "c", "d"],
        )
        self.assertTrue(appraisals)
        for appraisal in appraisals.values():
            for value in (
                appraisal.activation,
                appraisal.goal_conduciveness,
                appraisal.social_acceptability,
                appraisal.controllability,
                appraisal.identity_congruence,
            ):
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)
        for emotion in emotions:
            self.assertGreaterEqual(emotion.intensity, 0.0)
            self.assertLessEqual(emotion.intensity, 1.0)

    def test_kind_scores_are_bounded(self):
        engine = make_engine(meta_emotion_enabled=True)
        engine.state._last_update = 1000.0
        trace = run_tick(engine, severe_event())["trace"]
        self.assertTrue(trace["meta_emotions"])
        for emotion in trace["meta_emotions"]:
            self.assertGreaterEqual(emotion["intensity"], META_KIND_THRESHOLD)
            self.assertLessEqual(emotion["intensity"], 1.0)

    def test_neutral_profile_has_no_presets(self):
        self.assertEqual(NEUTRAL_META_PROFILE.kind_weights, {})
        self.assertEqual(NEUTRAL_META_PROFILE.action_bias, {})
        self.assertEqual(META_TARGET_CHANNELS, tuple(Channel))

    def test_multiple_kinds_coexist_without_cancellation(self):
        engine = make_engine(meta_emotion_enabled=True)
        engine.state._last_update = 1000.0
        trace = run_tick(engine, severe_event())["trace"]
        anger = anger_meta(trace)
        self.assertGreaterEqual(len(anger), 2)
        self.assertTrue(all(item["intensity"] > 0.0 for item in anger))
        self.assertGreater(len({item["kind"] for item in anger}), 1)


class MetaProfileTests(unittest.TestCase):
    def test_profile_weights_flip_anger_to_shame(self):
        profile = MetaEmotionProfile(
            name="shame",
            kind_weights={
                "anger": {
                    "shame": 2.0, "pride": 0.2,
                    "interest": 0.4, "fear": 0.8,
                },
            },
            action_bias={"anger": "suppress"},
        )
        engine = make_engine(
            meta_emotion_enabled=True, meta_emotion_profile=profile,
        )
        engine.state._last_update = 1000.0
        trace = run_tick(engine, severe_event())["trace"]
        anger = anger_meta(trace)
        self.assertTrue(anger)
        self.assertEqual(anger[0]["kind"], "shame")
        self.assertEqual(anger[0]["action"], "suppress")

    def test_profile_weights_flip_anger_to_pride(self):
        profile = MetaEmotionProfile(
            name="pride",
            kind_weights={
                "anger": {
                    "pride": 2.0, "shame": 0.2,
                    "interest": 0.4, "fear": 0.4,
                },
            },
            action_bias={"anger": "express"},
        )
        engine = make_engine(
            meta_emotion_enabled=True, meta_emotion_profile=profile,
        )
        engine.state._last_update = 1000.0
        trace = run_tick(engine, severe_event())["trace"]
        anger = anger_meta(trace)
        self.assertTrue(anger)
        self.assertEqual(anger[0]["kind"], "pride")
        self.assertEqual(anger[0]["action"], "express")

    def test_action_bias_is_additive_and_validated(self):
        self.assertEqual(ACTION_BIAS_BONUS, 0.25)

        # bias 只是 +0.25：能翻过 0.24 的差距，翻不过 0.26 的差距。
        scores = {name: 0.0 for name in META_ACTION_ORDER}
        scores["express"] = 0.50
        scores["suppress"] = 0.26
        suppress_bias = MetaEmotionProfile(
            action_bias={"anger": "suppress"},
        )
        self.assertEqual(_choose_action(scores, None, "anger"), "express")
        self.assertEqual(
            _choose_action(scores, suppress_bias, "anger"), "suppress",
        )
        scores["suppress"] = 0.24
        self.assertEqual(
            _choose_action(scores, suppress_bias, "anger"), "express",
        )

        # 未知 action / 属于其他 target 的 bias 不生效。
        self.assertEqual(
            _choose_action(
                scores,
                MetaEmotionProfile(action_bias={"anger": "bogus"}),
                "anger",
            ),
            "express",
        )
        self.assertEqual(
            _choose_action(
                scores,
                MetaEmotionProfile(action_bias={"joy": "suppress"}),
                "anger",
            ),
            "express",
        )

        # profile 权重限制在 [0, 2]，未列出的 kind 使用 1.0。
        capped = MetaEmotionProfile(
            kind_weights={"anger": {"shame": 5.0}},
        )
        self.assertEqual(_profile_weight(capped, "anger", "shame"), 2.0)
        self.assertEqual(_profile_weight(capped, "anger", "pride"), 1.0)
        self.assertEqual(_profile_weight(capped, "joy", "shame"), 1.0)

        personality = Personality()
        baseline = personality.baseline()
        state = state_at_baseline(personality)
        profile = MetaEmotionProfile(
            kind_weights={"anger": {"shame": 2.0}},
            action_bias={"anger": "suppress"},
        )
        appraisals, emotions = meta_appraise(
            state=state, baseline=baseline, personality=personality,
            profile=profile,
        )
        self.assertNotIn("anger", appraisals)
        self.assertFalse([
            emotion for emotion in emotions if emotion.target == "anger"
        ])

        unknown = MetaEmotionProfile(
            kind_weights={"bogus": {"shame": 2.0}},
            action_bias={"bogus": "suppress"},
        )
        self.assertEqual(
            meta_appraise(
                state=state, baseline=baseline, personality=personality,
            ),
            meta_appraise(
                state=state, baseline=baseline, personality=personality,
                profile=unknown,
            ),
        )

    def test_profile_does_not_change_dimensions_or_base_state(self):
        neutral = make_engine(meta_emotion_enabled=True)
        profile = MetaEmotionProfile(
            name="pride",
            kind_weights={"anger": {"pride": 2.0, "shame": 0.2}},
            action_bias={"anger": "express"},
        )
        profiled = make_engine(
            meta_emotion_enabled=True, meta_emotion_profile=profile,
        )
        neutral.state._last_update = 1000.0
        profiled.state._last_update = 1000.0
        neutral_result = run_tick(neutral, severe_event())
        profiled_result = run_tick(profiled, severe_event())

        self.assertEqual(neutral_result["state"], profiled_result["state"])
        self.assertEqual(
            neutral_result["tolerance"], profiled_result["tolerance"],
        )
        self.assertEqual(
            neutral_result["atmosphere"], profiled_result["atmosphere"],
        )
        self.assertEqual(
            neutral_result["trace"]["meta_appraisal"],
            profiled_result["trace"]["meta_appraisal"],
        )
        self.assertNotEqual(
            neutral_result["trace"]["meta_emotions"],
            profiled_result["trace"]["meta_emotions"],
        )


class MetaRegulationTests(unittest.TestCase):
    def test_expression_gain_and_display_mapping(self):
        engine = make_engine(meta_emotion_enabled=True)
        engine.state._last_update = 1000.0
        bias = run_tick(engine, severe_event())["trace"]["regulation_bias"]
        for target, gain in bias["expression_gain"].items():
            self.assertGreaterEqual(gain, 0.5)
            self.assertLessEqual(gain, 1.5)
            action = bias["regulation_action"][target]
            self.assertEqual(
                bias["display_rule"][target],
                ACTION_TO_DISPLAY.get(action, "neutral"),
            )

    def test_tolerance_load_bias_is_nonnegative_and_bounded(self):
        def emotion(action, intensity, target="anger"):
            return MetaEmotion(
                target=target, kind="pride", valence=0.8,
                intensity=intensity, acceptance=0.6, control=0.6,
                action=action,
            )

        express = meta_regulate({}, [emotion("express", 0.9)])
        self.assertEqual(express.tolerance_load_bias, 0.0)

        suppress = meta_regulate({}, [emotion("suppress", 0.5)])
        self.assertAlmostEqual(suppress.tolerance_load_bias, 0.03, places=6)

        many = meta_regulate(
            {},
            [
                emotion("suppress", 1.0, target=channel.value)
                for channel in Channel
            ],
        )
        self.assertGreaterEqual(many.tolerance_load_bias, 0.0)
        self.assertLessEqual(many.tolerance_load_bias, 0.2)

    def test_reappraisal_targets_are_known_and_deduped(self):
        emotions = [
            MetaEmotion(
                target="anger", kind="shame", valence=-0.8,
                intensity=0.8, acceptance=0.2, control=0.2,
                action="suppress",
            ),
            MetaEmotion(
                target="anger", kind="interest", valence=0.4,
                intensity=0.3, acceptance=0.2, control=0.2,
                action="suppress",
            ),
        ]
        bias = meta_regulate({}, emotions)
        self.assertEqual(
            list(bias.reappraisal_target),
            ["social_acceptability", "identity_congruence"],
        )
        self.assertEqual(len(set(bias.reappraisal_target)),
                         len(bias.reappraisal_target))
        self.assertLessEqual(len(bias.reappraisal_target), 2)
        for target in bias.reappraisal_target:
            self.assertIn(target, META_DIMENSIONS)


class MetaTraceSafetyTests(unittest.TestCase):
    def test_trace_meta_is_json_serializable_and_rounded(self):
        engine = make_engine(meta_emotion_enabled=True)
        engine.state._last_update = 1000.0
        trace = run_tick(engine, severe_event())["trace"]
        json.dumps(trace, ensure_ascii=False)

        for appraisal in trace["meta_appraisal"].values():
            for value in appraisal.values():
                self.assertEqual(round(value, 4), value)
        for emotion in trace["meta_emotions"]:
            for key in ("valence", "intensity", "acceptance", "control"):
                self.assertEqual(round(emotion[key], 4), emotion[key])
        for gain in trace["regulation_bias"]["expression_gain"].values():
            self.assertEqual(round(gain, 4), gain)
        self.assertEqual(
            round(trace["regulation_bias"]["tolerance_load_bias"], 4),
            trace["regulation_bias"]["tolerance_load_bias"],
        )

    def test_twenty_event_sequence_is_finite_and_bounded(self):
        engine = make_engine(meta_emotion_enabled=True)
        engine.state._last_update = 1000.0
        for index in range(20):
            appraisal = severe_event() if index % 2 == 0 else positive_event()
            now = engine.state._last_update + 60.0
            result = engine.tick(appraisal, now=now, trace=True)
            self.assertNotIn("meta_emotion", result)
            for value in result["state"].values():
                self.assertTrue(math.isfinite(value))
                self.assertGreaterEqual(value, 0.0)
            assert_meta_finite(self, result["trace"])

    def test_snapshot_does_not_persist_meta(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, "meta.db")
            engine = make_engine(
                meta_emotion_enabled=True,
                meta_emotion_profile=MetaEmotionProfile(
                    kind_weights={"anger": {"shame": 2.0}},
                ),
            )
            engine.state._last_update = 1000.0
            run_tick(engine, severe_event())
            self.assertTrue(save_snapshot(engine, path, now=1060.0))

            restored = make_engine()
            self.assertTrue(load_snapshot(restored, path))
            self.assertFalse(restored.meta_emotion_enabled)
            self.assertIsNone(restored.meta_emotion_profile)
            self.assertEqual(restored.state.to_dict(), engine.state.to_dict())

    def test_meta_module_import_boundary(self):
        path = pathlib.Path(meta_emotion.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        allowed = {"math", "dataclasses", "typing", "core", "__future__"}
        self.assertEqual(imported - allowed, set())
        forbidden = {
            "engine", "tolerance", "memory", "atmosphere",
            "sqlite3", "http", "requests", "urllib", "socket",
            "aiohttp", "httpx",
        }
        self.assertFalse(imported & forbidden)

    def test_meta_state_is_per_tick_and_not_stored_on_engine(self):
        engine = make_engine(meta_emotion_enabled=True)
        engine.state._last_update = 1000.0
        result = run_tick(engine, severe_event())
        self.assertFalse(hasattr(engine, "meta_state"))
        self.assertNotIn("meta_emotion", result)
        self.assertIn("meta_appraisal", result["trace"])
        self.assertTrue(result["trace"]["meta_enabled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
