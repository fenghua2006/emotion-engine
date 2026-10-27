"""L5.1 表达一致性 + 仪表盘耐受窗测试。"""

from __future__ import annotations

import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from engine import (
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SensitizationStore,
    _CHARACTER_POOLS,
    _ZONE_POOLS,
    _action_tendency,
    _expression_params,
    _select_local_utterance,
    respond,
)
from tolerance import (
    ZONE_HYPER,
    ZONE_HYPO,
    ZONE_REGULATED,
    ToleranceConfig,
    ToleranceState,
)

ROOT = pathlib.Path(__file__).resolve().parent


def closed_state(**values) -> EmotionalState:
    """所有通道先归零，避免 EmotionalState 的默认基线干扰判定。"""
    state = EmotionalState(
        joy=0.0, sadness=0.0, anger=0.0, fear=0.0, disgust=0.0,
        surprise=0.0, trust=0.0, love=0.0, longing=0.0, guilt=0.0,
    )
    for channel, value in values.items():
        setattr(state, channel, value)
    return state


def make_engine():
    personality = Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
        tolerance_config=ToleranceConfig(),
    )


class ActionTendencyTests(unittest.TestCase):
    def test_hypo_blunts_withdrawal_instead_of_amplifying_it(self):
        state = closed_state(joy=0.2, fear=0.28)
        self.assertEqual(
            _action_tendency(state, 0.0, ZONE_REGULATED), "withdraw",
        )
        self.assertEqual(
            _action_tendency(state, 0.0, ZONE_HYPO), "still",
        )

    def test_hypo_still_withdraws_when_avoidance_is_overwhelming(self):
        state = closed_state(joy=0.2, fear=1.0)
        self.assertEqual(
            _action_tendency(state, 0.0, ZONE_HYPO), "withdraw",
        )

    def test_hyper_suppresses_approach_impulse(self):
        state = closed_state(joy=0.35, fear=0.25)
        self.assertEqual(
            _action_tendency(state, 0.0, ZONE_REGULATED), "approach",
        )
        self.assertEqual(
            _action_tendency(state, 0.0, ZONE_HYPER), "still",
        )


class ExpressionTests(unittest.TestCase):
    def setUp(self):
        self.state = closed_state(
            joy=0.7, sadness=0.2, anger=0.2, fear=0.3,
            surprise=0.2, trust=0.5, love=0.3,
            longing=0.4, guilt=0.1,
        )

    def test_regulated_mapping_keeps_pre_l5_values(self):
        params = _expression_params(self.state, ZONE_REGULATED)
        self.assertEqual(params["brow_angle"], round(
            -0.5 + self.state.joy * 0.8 - self.state.sadness * 0.6, 3,
        ))
        self.assertEqual(params["mouth_curve"], round(
            -0.3 + self.state.joy * 0.7 - self.state.anger * 0.3, 3,
        ))
        self.assertEqual(params["eye_open"], round(
            0.8 + self.state.surprise * 0.4 - self.state.fear * 0.1, 3,
        ))

    def test_hyper_expression_is_tense_and_defensive(self):
        base = _expression_params(self.state, ZONE_REGULATED)
        hyper = _expression_params(self.state, ZONE_HYPER)
        self.assertLess(hyper["brow_angle"], base["brow_angle"])
        self.assertLess(hyper["mouth_curve"], base["mouth_curve"])
        self.assertGreater(hyper["eye_open"], base["eye_open"])
        self.assertLessEqual(hyper["blush"], base["blush"])
        self.assertLess(hyper["gaze_direction"], base["gaze_direction"])

    def test_hypo_expression_is_flat_and_low_arousal(self):
        base = _expression_params(self.state, ZONE_REGULATED)
        hypo = _expression_params(self.state, ZONE_HYPO)
        self.assertLess(hypo["eye_open"], base["eye_open"])
        self.assertLess(hypo["blush"], base["blush"])
        self.assertLessEqual(hypo["gaze_direction"], base["gaze_direction"])
        self.assertLessEqual(hypo["mouth_curve"], base["mouth_curve"])
        self.assertGreater(hypo["head_tilt"], base["head_tilt"])
        for value in hypo.values():
            self.assertGreaterEqual(value, -1.0)
            self.assertLessEqual(value, 1.0)


class LocalTemplateTests(unittest.TestCase):
    def test_zone_pool_overrides_dominant_emotion_pool(self):
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            regulated = _select_local_utterance(
                "furina", "joy_dominant", ZONE_REGULATED,
            )
            hyper = _select_local_utterance(
                "furina", "joy_dominant", ZONE_HYPER,
            )
            hypo = _select_local_utterance(
                "furina", "joy_dominant", ZONE_HYPO,
            )
        self.assertEqual(
            regulated, _CHARACTER_POOLS["furina"]["joy_dominant"][0],
        )
        self.assertEqual(hyper, _ZONE_POOLS["furina"]["hyper"][0])
        self.assertEqual(hypo, _ZONE_POOLS["furina"]["hypo"][0])

    def test_unknown_character_uses_default_zone_pool(self):
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            line = _select_local_utterance(
                "missing", "neutral", ZONE_HYPER,
            )
        self.assertEqual(line, _ZONE_POOLS["default"]["hyper"][0])

    def test_respond_uses_zone_for_utterance_expression_and_llm(self):
        engine = make_engine()
        engine.tolerance.zone = ZONE_HYPER
        with patch("random.choice", side_effect=lambda seq: seq[0]):
            result = respond(engine, "test")
        self.assertEqual(result["tolerance"]["zone"], ZONE_HYPER)
        self.assertEqual(
            result["utterance"], _ZONE_POOLS["default"]["hyper"][0],
        )
        self.assertEqual(
            result["expression"],
            _expression_params(engine.state, ZONE_HYPER),
        )
        self.assertTrue(result["needs_llm"])


class DashboardToleranceTests(unittest.TestCase):
    def test_history_point_keeps_tolerance_series_fields(self):
        from dashboard.server import tolerance_history_point

        source = {
            "timestamp": 123.0,
            "felt": {"joy": 0.4},
            "atmosphere": -0.2,
            "tolerance": {
                "load": 1.1,
                "capacity": 0.8,
                "zone": "hyper",
                "overload_ratio": 1.375,
                "capacity_model": "multi",
                "capacity_by_domain": {"anger": 0.7},
            },
        }
        point = tolerance_history_point(source)
        self.assertEqual(point["t"], 123.0)
        self.assertEqual(point["tolerance"]["zone"], "hyper")
        self.assertEqual(
            point["tolerance"]["capacity_by_domain"]["anger"], 0.7,
        )
        self.assertIsNot(point["felt"], source["felt"])
        self.assertIsNot(point["tolerance"], source["tolerance"])

    def test_state_response_exposes_multi_domain_tolerance(self):
        import dashboard.server as dashboard

        tolerance = ToleranceState()
        tolerance.load = 0.42
        tolerance.capacity = 0.86
        tolerance.capacity_by_domain = {
            "anger": 0.83, "fear": 0.88,
            "intimacy": 0.86, "criticism": 0.87,
        }
        fake_engine = SimpleNamespace(
            state=EmotionalState(),
            tolerance=tolerance,
            atmosphere=-0.3,
            scars=SimpleNamespace(all_scars=lambda: []),
            memory=SimpleNamespace(),
        )
        old = dict(dashboard._state)
        try:
            dashboard._state["engine"] = fake_engine
            dashboard._state["char_name"] = "Test"
            dashboard._state["char_id"] = "test"
            response = dashboard.build_state_response()
        finally:
            dashboard._state.clear()
            dashboard._state.update(old)

        self.assertEqual(response["tolerance"]["zone"], ZONE_REGULATED)
        self.assertEqual(
            set(response["tolerance"]["capacity_by_domain"]),
            {"anger", "fear", "intimacy", "criticism"},
        )
        self.assertAlmostEqual(response["tolerance"]["load"], 0.42)

    def test_frontend_wires_zone_band_and_domain_series(self):
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        for token in (
            'id="tolerance-chart"',
            'id="tol-zone"',
            "capacity_by_domain",
            "toleranceZones",
            "toleranceZoneBandPlugin",
            "TOL_DOMAINS",
        ):
            self.assertIn(token, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
