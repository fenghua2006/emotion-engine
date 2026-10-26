"""L1 六条快通道回归测试。

锁三件事：
  1. 固定人格 / 固定关系常数 / 固定 10 分钟步长下，六通道曲线不漂移；
  2. 计划中列出的 L1 不变量成立；
  3. L1.1 基线相对交互修复后，空闲交互地板消失，且 v0.7 旧曲线仍可复现。

golden 是当前行为基线，不是理论正确值。重新生成必须人工审阅 diff。
"""

from __future__ import annotations

import json
import math
import pathlib
import unittest

from core import Appraisal, Channel, Personality, appraise
from fastline import (
    DEFAULT_LOVE,
    DEFAULT_TRUST,
    DOMINANT_THRESHOLD,
    FastLine,
    SIX_CHANNELS,
    SIX_CHANNEL_NAMES,
)
from l1_events import EVENTS, EVENT_BY_KEY, events_by_source

ROOT = pathlib.Path(__file__).resolve().parent
GOLDEN_PATH = ROOT / "fixtures" / "l1_golden.json"
LEGACY_GOLDEN_PATH = ROOT / "fixtures" / "l1_golden_legacy.json"
TOL = 1e-9


def load_golden(path: pathlib.Path = GOLDEN_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


GOLDEN = load_golden()
LEGACY_GOLDEN = load_golden(LEGACY_GOLDEN_PATH)


class FixtureIntegrityTests(unittest.TestCase):
    def test_event_count_is_in_planned_range(self):
        self.assertGreaterEqual(len(EVENTS), 40)
        self.assertLessEqual(len(EVENTS), 60)
        self.assertEqual(len(EVENTS), 46)

    def test_event_keys_are_unique(self):
        keys = [event.key for event in EVENTS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_all_sources_are_represented(self):
        counts = {
            source: len(events_by_source(source))
            for source in ("furina", "kokomi", "columbina",
                           "engine-demo", "synthetic")
        }
        self.assertEqual(counts, {
            "furina": 11,
            "kokomi": 9,
            "columbina": 11,
            "engine-demo": 3,
            "synthetic": 12,
        })

    def test_appraisal_values_are_in_range(self):
        for event in EVENTS:
            app = event.appraisal
            self.assertGreaterEqual(app.goal_relevance, 0.0, event.key)
            self.assertLessEqual(app.goal_relevance, 1.0, event.key)
            self.assertGreaterEqual(app.goal_conduciveness, -1.0, event.key)
            self.assertLessEqual(app.goal_conduciveness, 1.0, event.key)
            self.assertGreaterEqual(app.expectedness, 0.0, event.key)
            self.assertLessEqual(app.expectedness, 1.0, event.key)
            self.assertGreaterEqual(app.coping_potential, 0.0, event.key)
            self.assertLessEqual(app.coping_potential, 1.0, event.key)
            self.assertGreaterEqual(app.other_agency, 0.0, event.key)
            self.assertLessEqual(app.other_agency, 1.0, event.key)
            self.assertGreaterEqual(app.social_evaluation, -1.0, event.key)
            self.assertLessEqual(app.social_evaluation, 1.0, event.key)

    def test_golden_meta_matches_reference_line(self):
        meta = GOLDEN["meta"]
        self.assertEqual(meta["layer"], "L1")
        self.assertEqual(meta["channels"], list(SIX_CHANNEL_NAMES))
        self.assertEqual(meta["event_count"], len(EVENTS))
        self.assertEqual(meta["trust"], DEFAULT_TRUST)
        self.assertEqual(meta["love"], DEFAULT_LOVE)
        self.assertEqual(meta["dominant_threshold"], DOMINANT_THRESHOLD)
        self.assertEqual(meta["interaction_mode"], "relative_to_baseline")
        self.assertEqual(
            LEGACY_GOLDEN["meta"]["interaction_mode"], "legacy_absolute",
        )
        self.assertEqual(meta["personality"], {
            "openness": 0.5,
            "conscientiousness": 0.5,
            "extraversion": 0.5,
            "agreeableness": 0.5,
            "neuroticism": 0.5,
        })

    def test_golden_covers_every_event(self):
        keys = {event.key for event in EVENTS}
        self.assertEqual(set(GOLDEN["isolated"]), keys)
        self.assertEqual(
            [item["key"] for item in GOLDEN["sequence"]],
            [event.key for event in EVENTS],
        )


class GoldenTests(unittest.TestCase):
    def assert_step_matches(self, key: str, step, expected: dict,
                            include_appraisal: bool = False):
        self.assertEqual(set(step.values), set(SIX_CHANNEL_NAMES), key)
        for channel in SIX_CHANNEL_NAMES:
            self.assertAlmostEqual(
                step.values[channel],
                expected["values"][channel],
                delta=TOL,
                msg=f"{key}.{channel}",
            )
        self.assertEqual(step.dominant, expected["dominant"], key)
        self.assertEqual(list(step.shock_channels),
                         expected["shock_channels"], key)
        self.assertEqual(list(step.blends), expected["blends"], key)
        if include_appraisal:
            for phase in ("raw_appraisal", "gated_appraisal"):
                for channel in SIX_CHANNEL_NAMES:
                    self.assertAlmostEqual(
                        getattr(step, phase)[channel],
                        expected[phase][channel],
                        delta=TOL,
                        msg=f"{key}.{phase}.{channel}",
                    )

    def test_isolated_golden(self):
        personality = Personality(**GOLDEN["meta"]["personality"])
        for event in EVENTS:
            line = FastLine(personality=personality)
            step = line.step(
                event.appraisal,
                elapsed_minutes=GOLDEN["meta"]["elapsed_minutes"],
                key=event.key,
            )
            self.assert_step_matches(
                event.key, step, GOLDEN["isolated"][event.key],
                include_appraisal=True,
            )

    def test_sequence_golden(self):
        personality = Personality(**GOLDEN["meta"]["personality"])
        line = FastLine(personality=personality)
        steps = line.run(
            EVENTS,
            elapsed_minutes=GOLDEN["meta"]["elapsed_minutes"],
        )
        self.assertEqual(len(steps), len(GOLDEN["sequence"]))
        for step, expected in zip(steps, GOLDEN["sequence"]):
            self.assert_step_matches(step.key, step, expected)

    def test_legacy_absolute_interactions_reproduce_v07(self):
        # L1.1 保留 legacy 开关，旧 golden 必须逐位复现，供修复前后对照。
        personality = Personality(**LEGACY_GOLDEN["meta"]["personality"])
        for event in EVENTS:
            line = FastLine(
                personality=personality,
                legacy_interactions=True,
                idle_interactions=False,
            )
            step = line.step(
                event.appraisal,
                elapsed_minutes=LEGACY_GOLDEN["meta"]["elapsed_minutes"],
                key=event.key,
            )
            self.assert_step_matches(
                event.key, step, LEGACY_GOLDEN["isolated"][event.key],
                include_appraisal=True,
            )

        line = FastLine(
            personality=personality,
            legacy_interactions=True,
            idle_interactions=False,
        )
        steps = line.run(
            EVENTS,
            elapsed_minutes=LEGACY_GOLDEN["meta"]["elapsed_minutes"],
        )
        for step, expected in zip(steps, LEGACY_GOLDEN["sequence"]):
            self.assert_step_matches(step.key, step, expected)

    def test_golden_is_reproducible(self):
        first = FastLine().run(EVENTS, elapsed_minutes=10.0)
        second = FastLine().run(EVENTS, elapsed_minutes=10.0)
        self.assertEqual(
            [step.values for step in first],
            [step.values for step in second],
        )
        self.assertEqual(
            [step.shock_channels for step in first],
            [step.shock_channels for step in second],
        )


class InvariantTests(unittest.TestCase):
    def test_six_channels_are_finite_and_non_negative(self):
        line = FastLine()
        for event in EVENTS:
            step = line.step(event.appraisal, elapsed_minutes=10.0)
            self.assertEqual(set(step.values), set(SIX_CHANNEL_NAMES))
            for channel, value in step.values.items():
                self.assertTrue(
                    math.isfinite(value),
                    f"{event.key}.{channel} not finite: {value}",
                )
                self.assertGreaterEqual(
                    value, 0.0, f"{event.key}.{channel} negative: {value}",
                )

    def test_no_event_decay_returns_to_baseline(self):
        line = FastLine()
        line.step(
            EVENT_BY_KEY["synthetic.unexpected_blow"].appraisal,
            elapsed_minutes=10.0,
        )
        before_idle = {
            ch.value: getattr(line.state, ch.value) for ch in SIX_CHANNELS
        }

        baseline = line.personality.baseline()
        line.step(None, elapsed_minutes=10.0)
        for ch in SIX_CHANNELS:
            value = getattr(line.state, ch.value)
            if before_idle[ch.value] > baseline[ch] + TOL:
                self.assertLess(
                    value,
                    before_idle[ch.value],
                    f"{ch.value} should decay without a new event",
                )
            else:
                self.assertAlmostEqual(
                    value, before_idle[ch.value], delta=TOL,
                    msg=f"{ch.value} should not move at baseline",
                )

        for _ in range(200):
            line.step(None, elapsed_minutes=10.0)

        for ch in SIX_CHANNELS:
            self.assertAlmostEqual(
                getattr(line.state, ch.value),
                baseline[ch],
                delta=1e-7,
                msg=f"{ch.value} did not return to baseline",
            )

    def test_pure_negative_event_has_zero_joy_in_appraisal(self):
        event = EVENT_BY_KEY["synthetic.pure_negative_zero_cues"]
        raw = appraise(event.appraisal)
        self.assertEqual(raw[Channel.JOY], 0.0)

    def test_expected_pure_negative_event_has_zero_joy_in_appraisal(self):
        expected_pure_negative = Appraisal(
            goal_relevance=1.0,
            goal_conduciveness=-0.8,
            expectedness=1.0,
            other_agency=0.5,
            coping_potential=0.0,
            social_evaluation=0.0,
        )
        raw = appraise(expected_pure_negative)
        self.assertEqual(raw[Channel.JOY], 0.0)
        self.assertEqual(raw[Channel.SURPRISE], 0.0)

    def test_unexpected_negative_event_does_not_create_joy(self):
        # L1.1: surprise→joy 只在 joy 已经高于基线时放大。
        # 纯负面事件的 joy 没有共激活，因此交互层不再凭空加 joy。
        event = EVENT_BY_KEY["synthetic.pure_negative_zero_cues"]
        self.assertEqual(appraise(event.appraisal)[Channel.JOY], 0.0)

        line = FastLine()
        step = line.step(event.appraisal, elapsed_minutes=10.0)
        baseline = line.personality.baseline()
        self.assertAlmostEqual(
            step.values["joy"], baseline[Channel.JOY], delta=1e-12,
        )

    def test_surprise_amplifies_existing_joy(self):
        # 共激活成立时，surprise→joy 仍然生效：正面且意外的事件
        # 比同等正面但完全预期的事件获得更多 joy。
        common = dict(
            goal_relevance=1.0,
            goal_conduciveness=0.8,
            other_agency=0.2,
            coping_potential=0.6,
            social_evaluation=0.5,
        )
        expected = FastLine().step(Appraisal(expectedness=1.0, **common))
        unexpected = FastLine().step(Appraisal(expectedness=0.1, **common))
        self.assertGreater(
            unexpected.values["joy"], expected.values["joy"],
        )

    def test_negative_event_with_positive_cues_can_have_bittersweet_joy(self):
        # 现有公式里 cp 与正社交评价会给少量 joy；这是有意保留的混合情绪，
        # 因此“不凭空创造 joy”只对零正面线索的纯负面事件成立。
        raw = appraise(Appraisal(
            goal_relevance=1.0,
            goal_conduciveness=-1.0,
            expectedness=0.5,
            other_agency=0.5,
            coping_potential=0.5,
            social_evaluation=0.0,
        ))
        self.assertGreater(raw[Channel.JOY], 0.0)

    def test_higher_unexpectedness_increases_surprise(self):
        common = dict(
            goal_relevance=1.0,
            goal_conduciveness=-0.5,
            other_agency=0.5,
            coping_potential=0.5,
            social_evaluation=0.0,
        )
        expected = Appraisal(expectedness=0.9, **common)
        unexpected = Appraisal(expectedness=0.1, **common)
        self.assertGreater(
            appraise(unexpected)[Channel.SURPRISE],
            appraise(expected)[Channel.SURPRISE],
        )

        expected_step = FastLine().step(expected, elapsed_minutes=10.0)
        unexpected_step = FastLine().step(unexpected, elapsed_minutes=10.0)
        self.assertGreater(
            unexpected_step.values["surprise"],
            expected_step.values["surprise"],
        )

    def test_low_coping_and_external_agency_raise_fear_anger_share(self):
        low_ext = EVENT_BY_KEY["synthetic.low_coping_external"].appraisal
        low_int = EVENT_BY_KEY["synthetic.low_coping_internal"].appraisal
        high_ext = EVENT_BY_KEY["synthetic.high_coping_external"].appraisal

        raw_low_ext = appraise(low_ext)
        raw_low_int = appraise(low_int)
        raw_high_ext = appraise(high_ext)

        # 外部归因提高 anger；低应对提高 fear。
        self.assertGreater(
            raw_low_ext[Channel.ANGER], raw_low_int[Channel.ANGER],
        )
        self.assertGreater(
            raw_low_ext[Channel.FEAR], raw_high_ext[Channel.FEAR],
        )

        def fear_anger_share(raw):
            negative = sum(raw[ch] for ch in (
                Channel.SADNESS, Channel.ANGER,
                Channel.FEAR, Channel.DISGUST,
            ))
            return (raw[Channel.FEAR] + raw[Channel.ANGER]) / negative

        self.assertGreater(
            fear_anger_share(raw_low_ext),
            fear_anger_share(raw_low_int),
        )
        self.assertGreater(
            fear_anger_share(raw_low_ext),
            fear_anger_share(raw_high_ext),
        )

    def test_slow_channels_are_restored_after_interactions(self):
        line = FastLine()
        line.step(EVENT_BY_KEY["furina.trial"].appraisal, elapsed_minutes=10.0)
        self.assertEqual(line.state.trust, DEFAULT_TRUST)
        self.assertEqual(line.state.love, DEFAULT_LOVE)
        self.assertEqual(line.state.guilt, 0.0)
        self.assertEqual(line.state.longing, 0.0)

    def test_idle_interactions_do_not_create_a_floor(self):
        # L1.1: 交互只由高于基线的激活驱动。即使每个空闲 tick 都跑
        # 交互矩阵，六通道也应稳定在基线，而不是被 surprise/disgust
        # 的基线值持续抬高。
        line = FastLine(idle_interactions=True)
        for _ in range(300):
            line.step(None, elapsed_minutes=10.0)

        baseline = line.personality.baseline()
        for ch in SIX_CHANNELS:
            self.assertAlmostEqual(
                getattr(line.state, ch.value), baseline[ch], delta=1e-9,
                msg=f"{ch.value} drifted at idle",
            )

    def test_legacy_absolute_interactions_reproduce_idle_floor(self):
        # 旧行为对照：surprise 基线 0.10、disgust 基线 0.06 都高于
        # 0.05 门槛，所以空闲时仍会抬高 joy / fear / anger。
        line = FastLine(
            legacy_interactions=True,
            idle_interactions=True,
        )
        for _ in range(300):
            line.step(None, elapsed_minutes=10.0)

        baseline = line.personality.baseline()
        self.assertGreater(line.state.joy, baseline[Channel.JOY] + 0.01)
        self.assertGreater(line.state.fear, baseline[Channel.FEAR] + 0.01)
        self.assertGreater(line.state.anger, baseline[Channel.ANGER] + 0.01)
        for ch in (Channel.SADNESS, Channel.DISGUST, Channel.SURPRISE):
            self.assertAlmostEqual(
                getattr(line.state, ch.value), baseline[ch], delta=1e-7,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
