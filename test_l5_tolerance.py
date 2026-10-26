"""L5 耐受校准测试：多域容量 / 伤疤映射 / meta 负荷 / golden / 快照。"""

from __future__ import annotations

import ast
import json
import math
import os
import pathlib
import random
import sqlite3
import tempfile
import unittest
from dataclasses import asdict, fields, replace
from types import SimpleNamespace
from unittest.mock import patch

import engine as engine_module
from engine import (
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SensitizationStore,
    load_snapshot,
    save_snapshot,
)
from l5_events import BASE, SCENARIOS, STEP_COUNT
from l5line import run_all, run_sequence
from tolerance import (
    CAPACITY_MODEL_MULTI,
    CAPACITY_MODEL_SCALAR,
    DOMAIN_CAPACITY_COEFFICIENTS,
    DOMAIN_NAMES,
    DOMAIN_WEIGHTS,
    META_LOAD_HARD_CAP,
    SCAR_DOMAIN_MAP,
    VALID_CAPACITY_MODELS,
    VALID_ZONES,
    ZONE_HYPER,
    ZONE_HYPO,
    ZONE_REGULATED,
    ToleranceConfig,
    ToleranceState,
)

ROOT = pathlib.Path(__file__).resolve().parent
CURRENT_FIXTURE = ROOT / "fixtures" / "l5_tolerance_golden.json"
LEGACY_FIXTURE = ROOT / "fixtures" / "l5_tolerance_golden_legacy.json"

OLD_KEYS = {
    "load", "capacity", "zone", "overload_ratio",
    "pending_zone", "pending_ticks",
}


def make_engine(*, personality=None, tolerance_config=None,
                legacy_tolerance=False, meta_emotion_enabled=False,
                tolerance_meta_load_enabled=False):
    kwargs = personality or {}
    pers = Personality(**kwargs)
    state = EmotionalState()
    for channel, value in pers.baseline().items():
        setattr(state, channel.value, value)
    return EmotionEngine(
        state=state,
        personality=pers,
        memory=MemoryStore(),
        scars=SensitizationStore(),
        tolerance_config=tolerance_config,
        legacy_tolerance=legacy_tolerance,
        meta_emotion_enabled=meta_emotion_enabled,
        tolerance_meta_load_enabled=tolerance_meta_load_enabled,
    )


def update(state, *, trust=0.25, love=0.20, neuroticism=0.5,
           atmosphere=0.0, scar_pressure=0.0, by_tag=None,
           meta_load_bias=0.0, elapsed_hours=0.0,
           config=None, model=None):
    tolerance = ToleranceState(config=config or ToleranceConfig())
    tolerance.update(
        state=dict(state),
        baseline=BASE,
        trust=trust,
        love=love,
        trust_target_value=None,
        neuroticism=neuroticism,
        atmosphere=atmosphere,
        shock_channels=(),
        boundary_violation=False,
        scar_pressure=scar_pressure,
        scar_pressure_by_tag=by_tag,
        meta_load_bias=meta_load_bias,
        elapsed_hours=elapsed_hours,
        capacity_model=model,
    )
    return tolerance


def capacity_for(trust, love, neuroticism, atmosphere, model,
                 scar_pressure=0.0):
    config = replace(ToleranceConfig(), capacity_model=model)
    return update(
        BASE, trust=trust, love=love, neuroticism=neuroticism,
        atmosphere=atmosphere, scar_pressure=scar_pressure,
        config=config,
    ).capacity


class FakeMeta:
    def __init__(self, bias=0.2):
        self.regulation_bias = SimpleNamespace(tolerance_load_bias=bias)
        self.bias = bias

    def to_trace_dict(self):
        return {
            "meta_appraisal": {},
            "meta_emotions": [],
            "regulation_bias": {
                "expression_gain": {},
                "display_rule": {},
                "regulation_action": {},
                "tolerance_load_bias": self.bias,
                "reappraisal_target": [],
            },
        }


class GoldenMixin:
    def assert_payload_equal(self, expected, actual, path="root"):
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict, path)
            self.assertEqual(set(expected), set(actual), path)
            for key in expected:
                self.assert_payload_equal(
                    expected[key], actual[key], f"{path}.{key}",
                )
            return
        if isinstance(expected, list):
            self.assertIsInstance(actual, list, path)
            self.assertEqual(len(expected), len(actual), path)
            for index, (left, right) in enumerate(zip(expected, actual)):
                self.assert_payload_equal(left, right, f"{path}[{index}]")
            return
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            self.assertAlmostEqual(expected, actual, delta=1e-9, msg=path)
            return
        self.assertEqual(expected, actual, path)


class GoldenTests(GoldenMixin, unittest.TestCase):
    def test_current_golden_matches_all_scenarios(self):
        expected = json.loads(CURRENT_FIXTURE.read_text(encoding="utf-8"))
        actual = run_all()
        self.assert_payload_equal(expected["scenarios"], actual)
        # run_sequence 与场景 runner 共享同一条更新路径。
        first = SCENARIOS[0]
        self.assert_payload_equal(
            actual[first.key]["steps"], run_sequence(first.steps),
        )

    def test_legacy_golden_matches_all_scenarios(self):
        expected = json.loads(LEGACY_FIXTURE.read_text(encoding="utf-8"))
        actual = run_all(legacy=True)
        self.assert_payload_equal(expected["scenarios"], actual)
        first = SCENARIOS[0]
        self.assert_payload_equal(
            actual[first.key]["steps"],
            run_sequence(first.steps, legacy=True),
        )

    def test_fixture_metadata_locks_l5_choices(self):
        meta = json.loads(
            CURRENT_FIXTURE.read_text(encoding="utf-8"),
        )["meta"]
        self.assertEqual(meta["layer"], "L5")
        self.assertEqual(meta["mode"], "current")
        self.assertEqual(meta["capacity_model"], CAPACITY_MODEL_MULTI)
        self.assertEqual(tuple(meta["domain_names"]), DOMAIN_NAMES)
        self.assertEqual(meta["domain_weights"], DOMAIN_WEIGHTS)
        self.assertEqual(
            meta["scar_domain_map"],
            {domain: list(tags) for domain, tags in SCAR_DOMAIN_MAP.items()},
        )
        self.assertEqual(meta["domain_scar_load"], 0.20)
        self.assertEqual(meta["meta_load_cap"], META_LOAD_HARD_CAP)
        self.assertEqual(meta["scenario_count"], len(SCENARIOS))
        self.assertEqual(meta["step_count"], STEP_COUNT)
        self.assertEqual(
            meta["generated_by"],
            "scripts/generate_l5_tolerance_golden.py",
        )
        config = meta["config"]
        self.assertEqual(config["capacity_model"], CAPACITY_MODEL_MULTI)
        self.assertEqual(config["domain_scar_load"], 0.20)
        self.assertEqual(config["meta_load_cap"], 0.20)

    def test_legacy_fixture_is_pre_l5_schema(self):
        payload = json.loads(LEGACY_FIXTURE.read_text(encoding="utf-8"))
        meta = payload["meta"]
        self.assertEqual(meta["mode"], "legacy")
        self.assertEqual(meta["capacity_model"], CAPACITY_MODEL_SCALAR)
        self.assertEqual(meta["scar_mode"], "global_max")
        self.assertFalse(meta["meta_load_consumed"])
        self.assertNotIn("capacity_model", meta["config"])
        for scenario in payload["scenarios"].values():
            for step in scenario["steps"]:
                self.assertEqual(set(step["tolerance"]), OLD_KEYS)
        calm = payload["scenarios"]["l5.calm_baseline"]["steps"][0]
        self.assertAlmostEqual(
            calm["tolerance"]["capacity"], 0.8575, places=9,
        )

    def test_scenario_keys_unique_and_counts(self):
        self.assertEqual(len(SCENARIOS), 15)
        self.assertEqual(STEP_COUNT, 41)
        scenario_keys = [scenario.key for scenario in SCENARIOS]
        self.assertEqual(len(scenario_keys), len(set(scenario_keys)))
        step_keys = [
            step.key for scenario in SCENARIOS for step in scenario.steps
        ]
        self.assertEqual(len(step_keys), len(set(step_keys)))
        config_fields = {item.name for item in fields(ToleranceConfig)}
        for scenario in SCENARIOS:
            self.assertTrue(set(scenario.config) <= config_fields)
            for step in scenario.steps:
                self.assertEqual(set(step.state), set(BASE))
                self.assertIn("trust", step.state)
                self.assertIn("love", step.state)


class DomainCapacityTests(unittest.TestCase):
    def test_domain_names_weights_and_sum(self):
        self.assertEqual(
            DOMAIN_NAMES, ("anger", "fear", "intimacy", "criticism"),
        )
        self.assertEqual(set(DOMAIN_WEIGHTS), set(DOMAIN_NAMES))
        self.assertAlmostEqual(sum(DOMAIN_WEIGHTS.values()), 1.0, places=12)
        self.assertEqual(
            set(DOMAIN_CAPACITY_COEFFICIENTS), set(DOMAIN_NAMES),
        )

    def test_weighted_coefficients_match_scalar_targets(self):
        targets = {
            "base": 0.75,
            "trust": 0.28,
            "love": 0.1375,
            "neuro": 0.205,
            "atmosphere_positive": 0.102,
            "atmosphere_negative": 0.0495,
        }
        for name, target in targets.items():
            weighted = sum(
                DOMAIN_WEIGHTS[domain]
                * getattr(DOMAIN_CAPACITY_COEFFICIENTS[domain], name)
                for domain in DOMAIN_NAMES
            )
            self.assertAlmostEqual(weighted, target, places=12)

    def test_domain_capacity_hand_computed(self):
        tolerance = update(
            BASE, trust=0.5, love=0.25, neuroticism=0.7,
            atmosphere=0.4, by_tag={"criticism": 0.5},
        )
        expected = {
            "anger": 0.708,
            "fear": 0.85925,
            "intimacy": 0.8513,
            "criticism": 0.7648,
        }
        for domain, value in expected.items():
            self.assertAlmostEqual(
                tolerance.capacity_by_domain[domain], value, places=12,
                msg=domain,
            )
        self.assertAlmostEqual(
            tolerance.capacity,
            0.25 * 0.708 + 0.30 * 0.85925
            + 0.25 * 0.8513 + 0.20 * 0.7648,
            places=12,
        )

    def test_effective_capacity_is_weighted_sum_of_clamped_domains(self):
        config = replace(ToleranceConfig(), base_capacity=1.70)
        tolerance = update(
            BASE, trust=1.0, love=1.0, neuroticism=0.0,
            atmosphere=-1.0, config=config,
        )
        domains = tolerance.capacity_by_domain
        self.assertEqual(set(domains), set(DOMAIN_NAMES))
        for value in domains.values():
            self.assertAlmostEqual(value, config.max_capacity, places=12)
        expected = sum(
            DOMAIN_WEIGHTS[domain] * domains[domain]
            for domain in DOMAIN_NAMES
        )
        self.assertAlmostEqual(tolerance.capacity, expected, places=12)

    def test_scalar_capacity_is_bit_identical_to_old_formula(self):
        config = replace(ToleranceConfig(), capacity_model=CAPACITY_MODEL_SCALAR)
        tolerance = update(
            BASE, trust=0.37, love=0.82, neuroticism=0.63,
            atmosphere=0.47, scar_pressure=0.58, config=config,
        )
        expected = (
            config.base_capacity
            + config.trust_capacity * 0.37
            + config.love_capacity * 0.82
        )
        expected *= 1.0 - config.neuroticism_narrowing * (0.63 - 0.5)
        expected -= 0.10 * 0.47
        expected -= config.scar_load * 0.58
        self.assertAlmostEqual(tolerance.capacity, expected, places=12)
        self.assertEqual(tolerance.capacity_by_domain, {})

    def test_trust_love_specialization(self):
        low = update(BASE, trust=0.0, love=0.0).capacity_by_domain
        trust_high = update(BASE, trust=0.5, love=0.0).capacity_by_domain
        self.assertAlmostEqual(
            trust_high["fear"] - low["fear"], 0.36 * 0.5, places=12,
        )
        self.assertAlmostEqual(
            trust_high["criticism"] - low["criticism"], 0.36 * 0.5,
            places=12,
        )
        self.assertAlmostEqual(
            trust_high["intimacy"] - low["intimacy"], 0.20 * 0.5,
            places=12,
        )
        self.assertGreater(
            trust_high["fear"] - low["fear"],
            trust_high["intimacy"] - low["intimacy"],
        )

        love_high = update(BASE, trust=0.0, love=0.5).capacity_by_domain
        self.assertAlmostEqual(
            love_high["intimacy"] - low["intimacy"], 0.25 * 0.5, places=12,
        )
        self.assertAlmostEqual(
            love_high["fear"] - low["fear"], 0.10 * 0.5, places=12,
        )
        self.assertGreater(
            love_high["intimacy"] - low["intimacy"],
            love_high["fear"] - low["fear"],
        )

    def test_neuroticism_and_atmosphere_specialization(self):
        neutral = update(BASE).capacity_by_domain
        tense = update(BASE, neuroticism=0.9).capacity_by_domain
        fear_drop = neutral["fear"] - tense["fear"]
        anger_drop = neutral["anger"] - tense["anger"]
        intimacy_drop = neutral["intimacy"] - tense["intimacy"]
        criticism_drop = neutral["criticism"] - tense["criticism"]
        self.assertGreater(fear_drop, anger_drop)
        self.assertGreater(criticism_drop, intimacy_drop)

        stressed = update(BASE, atmosphere=0.6).capacity_by_domain
        atm_drops = {
            domain: neutral[domain] - stressed[domain]
            for domain in DOMAIN_NAMES
        }
        self.assertGreater(atm_drops["criticism"], atm_drops["fear"])
        self.assertGreater(atm_drops["fear"], atm_drops["anger"])
        self.assertAlmostEqual(
            atm_drops["anger"], atm_drops["intimacy"], places=12,
        )

    def test_default_capacity_drift_is_bounded(self):
        cases = [
            (0.25, 0.20, 0.5, -0.3, 0.01),
            (1.0, 1.0, 0.5, -0.3, 0.03),
            (0.25, 0.20, 0.85, 0.6, 0.02),
        ]
        for trust, love, neuro, atmosphere, bound in cases:
            scalar = capacity_for(
                trust, love, neuro, atmosphere, CAPACITY_MODEL_SCALAR,
            )
            multi = capacity_for(
                trust, love, neuro, atmosphere, CAPACITY_MODEL_MULTI,
            )
            self.assertLess(
                abs(multi - scalar), bound,
                msg=(trust, love, neuro, atmosphere, multi, scalar),
            )


class ScarCompressionTests(unittest.TestCase):
    def test_scar_uses_max_of_mapped_tags(self):
        base = update(BASE).capacity_by_domain
        both = update(
            BASE, by_tag={"social_hurt": 0.4, "criticism": 0.6},
        ).capacity_by_domain
        only = update(
            BASE, by_tag={"social_hurt": 0.4},
        ).capacity_by_domain
        # anger 同时映射 social_hurt / criticism，取 .6 而不是相加。
        self.assertAlmostEqual(
            base["anger"] - both["anger"], 0.20 * 0.6, places=12,
        )
        self.assertAlmostEqual(
            base["anger"] - only["anger"], 0.20 * 0.4, places=12,
        )
        self.assertGreater(base["anger"] - both["anger"],
                           base["anger"] - only["anger"])

    def test_event_type_scar_compression_is_targeted(self):
        base = update(BASE).capacity_by_domain

        social = update(
            BASE, by_tag={"social_hurt": 0.6},
        ).capacity_by_domain
        for domain in ("anger", "fear", "criticism"):
            self.assertAlmostEqual(
                base[domain] - social[domain], 0.20 * 0.6, places=12,
            )
        self.assertAlmostEqual(
            base["intimacy"], social["intimacy"], places=12,
        )

        blame = update(
            BASE, by_tag={"self_blame": 0.6},
        ).capacity_by_domain
        self.assertAlmostEqual(
            base["intimacy"] - blame["intimacy"], 0.20 * 0.6, places=12,
        )
        for domain in ("anger", "fear", "criticism"):
            self.assertAlmostEqual(base[domain], blame[domain], places=12)

        criticism = update(
            BASE, by_tag={"criticism": 0.6},
        ).capacity_by_domain
        for domain in ("anger", "criticism"):
            self.assertAlmostEqual(
                base[domain] - criticism[domain], 0.20 * 0.6, places=12,
            )
        for domain in ("fear", "intimacy"):
            self.assertAlmostEqual(
                base[domain], criticism[domain], places=12,
            )

        cold = update(
            BASE, by_tag={"cold_shoulder": 0.6},
        ).capacity_by_domain
        for domain in ("fear", "intimacy"):
            self.assertAlmostEqual(
                base[domain] - cold[domain], 0.20 * 0.6, places=12,
            )
        for domain in ("anger", "criticism"):
            self.assertAlmostEqual(base[domain], cold[domain], places=12)

    def test_scar_map_none_falls_back_to_global_pressure(self):
        base = update(BASE).capacity_by_domain
        global_scar = update(
            BASE, scar_pressure=0.6, by_tag=None,
        ).capacity_by_domain
        for domain in DOMAIN_NAMES:
            self.assertAlmostEqual(
                base[domain] - global_scar[domain], 0.20 * 0.6,
                places=12, msg=domain,
            )

    def test_empty_or_unknown_scar_map_has_no_domain_compression(self):
        base = update(BASE).capacity_by_domain
        for by_tag in ({}, {"unknown_event": 0.8}):
            other = update(
                BASE, scar_pressure=0.6, by_tag=by_tag,
            ).capacity_by_domain
            for domain in DOMAIN_NAMES:
                self.assertAlmostEqual(
                    base[domain], other[domain], places=12, msg=domain,
                )

    def test_scalar_model_ignores_scar_map_and_meta_bias(self):
        config = replace(ToleranceConfig(), capacity_model=CAPACITY_MODEL_SCALAR)
        plain = update(BASE, config=config, scar_pressure=0.6)
        extra = update(
            BASE, config=config, scar_pressure=0.6,
            by_tag={"criticism": 0.6}, meta_load_bias=0.2,
        )
        self.assertAlmostEqual(plain.capacity, extra.capacity, places=12)
        self.assertAlmostEqual(plain.load, extra.load, places=12)
        self.assertEqual(extra.capacity_by_domain, {})
        self.assertEqual(extra.meta_load_bias, 0.0)


class MetaLoadTests(unittest.TestCase):
    def test_meta_bias_is_additive_and_capped(self):
        for bias, expected in ((0.0, 0.0), (0.05, 0.05),
                               (0.20, 0.20), (0.50, 0.20)):
            tolerance = update(BASE, meta_load_bias=bias)
            self.assertAlmostEqual(tolerance.load, expected, places=12)
            self.assertAlmostEqual(
                tolerance.meta_load_bias, expected, places=12,
            )

    def test_meta_bias_does_not_accumulate(self):
        tolerance = ToleranceState()
        for _ in range(3):
            tolerance.update(
                state=dict(BASE), baseline=BASE, trust=0.25, love=0.20,
                trust_target_value=None, neuroticism=0.5,
                meta_load_bias=0.20, elapsed_hours=0.0,
            )
            self.assertAlmostEqual(tolerance.load, 0.20, places=12)
        self.assertAlmostEqual(tolerance.load, 0.20, places=12)

    def test_meta_load_cap_can_lower_not_raise(self):
        lowered = replace(ToleranceConfig(), meta_load_cap=0.10)
        tolerance = update(BASE, meta_load_bias=0.5, config=lowered)
        self.assertAlmostEqual(tolerance.meta_load_bias, 0.10, places=12)

        raised = replace(ToleranceConfig(), meta_load_cap=0.50)
        tolerance = update(BASE, meta_load_bias=0.5, config=raised)
        self.assertAlmostEqual(
            tolerance.meta_load_bias, META_LOAD_HARD_CAP, places=12,
        )

    def test_engine_meta_load_requires_meta_emotion_enabled(self):
        engine = make_engine(
            tolerance_meta_load_enabled=True,
            meta_emotion_enabled=False,
        )
        with patch.object(
            engine_module, "evaluate_meta_emotion",
            side_effect=lambda **_: FakeMeta(),
        ) as mocked:
            result = engine.tick(now=engine.state._last_update + 60)
        self.assertEqual(mocked.call_count, 0)
        self.assertLess(result["tolerance"]["load"], 0.01)
        self.assertEqual(result["tolerance"]["meta_load_bias"], 0.0)

    def test_engine_legacy_tolerance_ignores_meta_load(self):
        engine = make_engine(
            legacy_tolerance=True,
            meta_emotion_enabled=True,
            tolerance_meta_load_enabled=True,
        )
        with patch.object(
            engine_module, "evaluate_meta_emotion",
            side_effect=lambda **_: FakeMeta(),
        ) as mocked:
            result = engine.tick(now=engine.state._last_update + 60)
        self.assertEqual(mocked.call_count, 0)
        self.assertEqual(result["tolerance"]["zone"], ZONE_REGULATED)
        self.assertNotIn("capacity_model", result["tolerance"])
        self.assertLess(result["tolerance"]["load"], 0.01)
        self.assertEqual(engine.tolerance.capacity_model, CAPACITY_MODEL_SCALAR)
        self.assertEqual(engine.tolerance.capacity_by_domain, {})

    def test_engine_meta_bias_uses_previous_tolerance_context(self):
        engine = make_engine(
            meta_emotion_enabled=True,
            tolerance_meta_load_enabled=True,
        )
        engine.tolerance.zone = ZONE_HYPER
        engine.tolerance.load = 1.2
        engine.tolerance.capacity = 0.8
        engine.tolerance.overload_ratio = 1.5
        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return FakeMeta(bias=0.0)

        with patch.object(engine_module, "evaluate_meta_emotion",
                          side_effect=fake):
            engine.tick(now=engine.state._last_update)

        self.assertEqual(captured["tolerance_zone"], ZONE_HYPER)
        self.assertAlmostEqual(captured["overload_ratio"], 1.5)

    def test_engine_meta_bias_computed_once(self):
        cases = [
            (True, True, 1),
            (True, False, 1),
            (False, True, 1),
            (False, False, 0),
        ]
        for trace, load_flag, expected in cases:
            engine = make_engine(
                meta_emotion_enabled=True,
                tolerance_meta_load_enabled=load_flag,
            )
            with patch.object(
                engine_module, "evaluate_meta_emotion",
                side_effect=lambda **_: FakeMeta(),
            ) as mocked:
                engine.tick(now=engine.state._last_update + 60, trace=trace)
            self.assertEqual(
                mocked.call_count, expected,
                msg=(trace, load_flag),
            )

    def test_engine_meta_bias_increases_load(self):
        engine = make_engine(
            meta_emotion_enabled=True,
            tolerance_meta_load_enabled=True,
        )
        with patch.object(
            engine_module, "evaluate_meta_emotion",
            side_effect=lambda **_: FakeMeta(bias=0.2),
        ):
            result = engine.tick(
                now=engine.state._last_update + 60, trace=True,
            )
        tolerance = result["tolerance"]
        self.assertEqual(tolerance["capacity_model"], CAPACITY_MODEL_MULTI)
        self.assertAlmostEqual(tolerance["load"], 0.2, delta=0.01)
        self.assertAlmostEqual(tolerance["meta_load_bias"], 0.2, places=12)
        self.assertEqual(tolerance["zone"], ZONE_REGULATED)
        self.assertEqual(result["trace"]["tolerance_zone"], ZONE_REGULATED)

    def test_meta_bias_does_not_double_count_scar(self):
        tolerance = ToleranceState()
        kwargs = dict(
            state=dict(BASE), baseline=BASE, trust=0.25, love=0.20,
            trust_target_value=None, neuroticism=0.5, atmosphere=0.0,
            scar_pressure=0.6, scar_pressure_by_tag={"criticism": 0.6},
            elapsed_hours=0.0,
        )
        tolerance.update(**kwargs)
        capacity = tolerance.capacity
        domains = dict(tolerance.capacity_by_domain)
        tolerance.update(meta_load_bias=0.2, **kwargs)
        self.assertAlmostEqual(tolerance.capacity, capacity, places=12)
        self.assertEqual(tolerance.capacity_by_domain, domains)
        self.assertAlmostEqual(tolerance.load, 0.2, places=12)

    def test_meta_bias_alone_never_overloads(self):
        tolerance = update(BASE, meta_load_bias=0.2)
        self.assertEqual(tolerance.zone, ZONE_REGULATED)
        self.assertLess(tolerance.overload_ratio, 1.0)
        self.assertGreaterEqual(tolerance.capacity, 0.35)


class SerializationTests(unittest.TestCase):
    def test_multi_tolerance_dict_schema_and_roundtrip(self):
        tolerance = update(
            BASE, scar_pressure=0.6,
            by_tag={"criticism": 0.6}, meta_load_bias=0.2,
        )
        data = tolerance.to_dict()
        self.assertEqual(set(data), OLD_KEYS | {
            "capacity_model", "capacity_by_domain", "meta_load_bias",
        })
        self.assertEqual(data["capacity_model"], CAPACITY_MODEL_MULTI)
        self.assertEqual(set(data["capacity_by_domain"]), set(DOMAIN_NAMES))
        restored = ToleranceState.from_dict(data)
        self.assertEqual(restored.capacity_model, CAPACITY_MODEL_MULTI)
        for domain in DOMAIN_NAMES:
            self.assertAlmostEqual(
                restored.capacity_by_domain[domain],
                data["capacity_by_domain"][domain], places=12,
            )
        self.assertAlmostEqual(
            restored.meta_load_bias, data["meta_load_bias"], places=12,
        )
        self.assertEqual(restored.zone, tolerance.zone)

    def test_scalar_tolerance_dict_has_only_old_keys(self):
        config = replace(ToleranceConfig(), capacity_model=CAPACITY_MODEL_SCALAR)
        tolerance = update(BASE, config=config)
        self.assertEqual(set(tolerance.to_dict()), OLD_KEYS)
        self.assertEqual(tolerance.to_dict()["capacity"], round(
            tolerance.capacity, 4,
        ))

    def test_from_dict_old_and_invalid_payloads(self):
        old = {
            "load": 1.1,
            "capacity": 0.8,
            "zone": ZONE_HYPER,
            "overload_ratio": 1.375,
            "pending_zone": ZONE_HYPO,
            "pending_ticks": 1,
        }
        restored = ToleranceState.from_dict(old)
        self.assertEqual(restored.capacity_model, CAPACITY_MODEL_MULTI)
        self.assertEqual(restored.capacity_by_domain, {})
        self.assertEqual(restored.meta_load_bias, 0.0)
        self.assertAlmostEqual(restored.load, 1.1)
        self.assertAlmostEqual(restored.capacity, 0.8)

        scalar_config = replace(
            ToleranceConfig(), capacity_model=CAPACITY_MODEL_SCALAR,
        )
        self.assertEqual(
            ToleranceState(
                config=scalar_config, capacity_model="bogus",
            ).capacity_model,
            CAPACITY_MODEL_SCALAR,
        )
        restored = ToleranceState.from_dict(
            {**old, "capacity_model": "bogus", "capacity_by_domain": {
                "fear": 1.2,
            }},
            config=scalar_config,
        )
        self.assertEqual(restored.capacity_model, CAPACITY_MODEL_SCALAR)
        self.assertEqual(restored.capacity_by_domain, {})

        invalid = {
            "load": 99,
            "capacity": -5,
            "zone": "unknown",
            "overload_ratio": -1,
            "pending_zone": "bogus",
            "pending_ticks": "x",
            "capacity_model": "multi",
            "capacity_by_domain": {
                "anger": 99,
                "fear": float("nan"),
                "intimacy": "bad",
                "criticism": -1,
                "rage": 0.9,
            },
            "meta_load_bias": 99,
        }
        restored = ToleranceState.from_dict(invalid)
        self.assertEqual(restored.zone, ZONE_REGULATED)
        self.assertIsNone(restored.pending_zone)
        self.assertEqual(restored.pending_ticks, 0)
        self.assertAlmostEqual(restored.load, 3.0)
        self.assertAlmostEqual(restored.capacity, 0.35)
        self.assertEqual(restored.overload_ratio, 0.0)
        self.assertEqual(set(restored.capacity_by_domain), {
            "anger", "criticism",
        })
        self.assertAlmostEqual(restored.capacity_by_domain["anger"], 1.8)
        self.assertAlmostEqual(restored.capacity_by_domain["criticism"], 0.35)
        self.assertAlmostEqual(
            restored.meta_load_bias, META_LOAD_HARD_CAP,
        )


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ee_l5_")
        self.path = os.path.join(self.tmpdir, "snapshot.db")

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)
        os.rmdir(self.tmpdir)

    def _payload(self):
        db = sqlite3.connect(self.path)
        try:
            row = db.execute(
                "SELECT state_json FROM snapshots "
                "ORDER BY saved_at DESC LIMIT 1",
            ).fetchone()
        finally:
            db.close()
        return json.loads(row[0])

    def test_snapshot_roundtrips_legacy_tolerance_flag_and_model(self):
        engine = make_engine(legacy_tolerance=True)
        engine.tolerance.load = 1.0
        engine.tolerance.capacity = 0.85
        engine.tolerance.zone = ZONE_HYPER
        engine.tolerance.overload_ratio = 1.176
        self.assertTrue(save_snapshot(engine, self.path, now=123456.0))

        restored = make_engine()
        self.assertFalse(restored.legacy_tolerance)
        self.assertTrue(load_snapshot(restored, self.path))
        self.assertTrue(restored.legacy_tolerance)
        self.assertEqual(
            restored.tolerance.capacity_model, CAPACITY_MODEL_SCALAR,
        )
        self.assertEqual(restored.tolerance.capacity_by_domain, {})
        self.assertEqual(restored.tolerance.zone, ZONE_HYPER)
        self.assertAlmostEqual(restored.tolerance.load, 1.0)

    def test_snapshot_roundtrips_domain_map_and_applied_bias(self):
        engine = make_engine()
        engine.tolerance.capacity_model = CAPACITY_MODEL_MULTI
        engine.tolerance.capacity = 0.81
        engine.tolerance.capacity_by_domain = {
            "anger": 0.70, "fear": 0.86,
            "intimacy": 0.85, "criticism": 0.75,
        }
        engine.tolerance.meta_load_bias = 0.2
        engine.tolerance.load = 0.2
        self.assertTrue(save_snapshot(engine, self.path, now=123456.0))

        restored = make_engine()
        self.assertTrue(load_snapshot(restored, self.path))
        self.assertEqual(
            restored.tolerance.capacity_model, CAPACITY_MODEL_MULTI,
        )
        for domain, value in engine.tolerance.capacity_by_domain.items():
            self.assertAlmostEqual(
                restored.tolerance.capacity_by_domain[domain], value,
                places=12,
            )
        self.assertAlmostEqual(restored.tolerance.meta_load_bias, 0.2)

    def test_snapshot_does_not_persist_meta_flags(self):
        engine = make_engine(
            meta_emotion_enabled=True,
            tolerance_meta_load_enabled=True,
        )
        self.assertTrue(save_snapshot(engine, self.path, now=123456.0))
        payload = self._payload()
        self.assertNotIn("tolerance_meta_load_enabled", payload)
        self.assertNotIn("meta_emotion_enabled", payload)
        self.assertNotIn("meta_emotion_profile", payload)
        self.assertIn("legacy_tolerance", payload)

        restored = make_engine(tolerance_meta_load_enabled=False)
        self.assertTrue(load_snapshot(restored, self.path))
        self.assertFalse(restored.tolerance_meta_load_enabled)

    def test_custom_tolerance_config_survives_load_when_reprovided(self):
        config = replace(
            ToleranceConfig(), base_capacity=0.90, max_capacity=1.90,
        )
        engine = make_engine(tolerance_config=config)
        engine.tolerance.capacity = 1.23
        engine.tolerance.zone = ZONE_HYPO
        engine.tolerance.load = 0.4
        self.assertTrue(save_snapshot(engine, self.path, now=123456.0))

        restored = make_engine(tolerance_config=config)
        self.assertTrue(load_snapshot(restored, self.path))
        self.assertIs(restored.tolerance_config, config)
        self.assertIs(restored.tolerance.config, config)
        self.assertAlmostEqual(restored.tolerance.capacity, 1.23)
        self.assertEqual(
            restored.tolerance.capacity_model, CAPACITY_MODEL_MULTI,
        )


class InvariantTests(unittest.TestCase):
    def test_low_load_never_hypo_and_recovery_hysteresis_hold(self):
        calm = update(BASE)
        self.assertEqual(calm.zone, ZONE_REGULATED)
        self.assertNotEqual(calm.zone, ZONE_HYPO)

        tolerance = ToleranceState()
        tolerance.zone = ZONE_HYPER
        tolerance.load = 0.5
        tolerance.capacity = 1.0
        tolerance.overload_ratio = 0.5
        kwargs = dict(
            state=dict(BASE), baseline=BASE, trust=0.25, love=0.20,
            trust_target_value=None, neuroticism=0.5,
            elapsed_hours=100.0,
        )
        tolerance.update(**kwargs)
        self.assertEqual(tolerance.zone, ZONE_HYPER)
        self.assertEqual(tolerance.pending_zone, ZONE_REGULATED)
        self.assertEqual(tolerance.pending_ticks, 1)
        tolerance.update(**kwargs)
        self.assertEqual(tolerance.zone, ZONE_REGULATED)
        self.assertIsNone(tolerance.pending_zone)

    def test_fuzz_update_is_finite_and_bounded(self):
        rng = random.Random(1234)
        tags = tuple(SCAR_DOMAIN_MAP["anger"]) + (
            "cold_shoulder", "self_blame",
        )
        channels = tuple(BASE)
        for _ in range(500):
            state = {channel: rng.uniform(-2.0, 3.0) for channel in channels}
            tolerance = update(
                state,
                trust=rng.uniform(-1.0, 2.0),
                love=rng.uniform(-1.0, 2.0),
                neuroticism=rng.uniform(-1.0, 2.0),
                atmosphere=rng.uniform(-3.0, 3.0),
                scar_pressure=rng.uniform(-1.0, 2.0),
                by_tag={tag: rng.uniform(-1.0, 2.0) for tag in tags},
                meta_load_bias=rng.uniform(-1.0, 3.0),
                elapsed_hours=rng.uniform(-1.0, 100.0),
            )
            self.assertIn(tolerance.zone, VALID_ZONES)
            self.assertTrue(math.isfinite(tolerance.capacity))
            self.assertTrue(math.isfinite(tolerance.load))
            self.assertTrue(math.isfinite(tolerance.overload_ratio))
            self.assertGreaterEqual(tolerance.capacity, 0.35 - 1e-12)
            self.assertLessEqual(tolerance.capacity, 1.80 + 1e-12)
            self.assertGreaterEqual(tolerance.load, 0.0)
            self.assertLessEqual(tolerance.load, 3.0)
            self.assertGreaterEqual(tolerance.overload_ratio, 0.0)
            self.assertGreaterEqual(tolerance.meta_load_bias, 0.0)
            self.assertLessEqual(
                tolerance.meta_load_bias, META_LOAD_HARD_CAP + 1e-12,
            )
            self.assertEqual(
                set(tolerance.capacity_by_domain), set(DOMAIN_NAMES),
            )
            for value in tolerance.capacity_by_domain.values():
                self.assertTrue(math.isfinite(value))
                self.assertGreaterEqual(value, 0.35 - 1e-12)
                self.assertLessEqual(value, 1.80 + 1e-12)
            self.assertGreaterEqual(tolerance.pending_ticks, 0)

    def test_tolerance_module_import_boundary(self):
        tree = ast.parse(
            (ROOT / "tolerance.py").read_text(encoding="utf-8"),
        )
        allowed = {"__future__", "math", "dataclasses", "typing"}
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(
                    alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        self.assertTrue(imported <= allowed, imported - allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
