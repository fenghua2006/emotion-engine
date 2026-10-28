"""L7 网页实验台的服务器端辅助函数测试。

这些测试不启动 HTTP 服务，直接验证配置解析、隔离引擎、tick 编排和
返回给前端的 payload。HTTP 路由只是薄封装，手工用浏览器即可覆盖。
"""

import unittest

from contagion import ContagionEdge, ContagionNetwork, relationship_strength
from dashboard import server


NEGATIVE = {
    "goal_relevance": 0.8,
    "goal_conduciveness": -0.6,
    "expectedness": 0.2,
    "coping_potential": 0.2,
    "other_agency": 0.6,
    "social_evaluation": -0.4,
}
POSITIVE = {
    "goal_relevance": 0.8,
    "goal_conduciveness": 0.6,
    "expectedness": 0.3,
    "coping_potential": 0.6,
    "other_agency": 0.4,
    "social_evaluation": 0.7,
}
NEUTRAL = {
    "goal_relevance": 0.4,
    "goal_conduciveness": 0.0,
    "expectedness": 0.5,
    "coping_potential": 0.5,
    "other_agency": 0.2,
    "social_evaluation": 0.0,
}


def network_config():
    return {
        "mode": "network",
        "gain": 0.2,
        "cap": 0.08,
        "hop_decay": 0.5,
        "max_hops": 3,
        "minutes_per_step": 10,
        "nodes": [
            {"id": "A", "character": "furina", "trust": 0.8, "love": 1.2,
             "familiarity": 0.8, "boundaries": ["不深夜打扰"]},
            {"id": "B", "character": "kokomi", "trust": 0.6, "love": 0.6,
             "familiarity": 0.5},
            {"id": "C", "character": "columbina", "trust": 0.3, "love": 0.3,
             "familiarity": 0.2},
        ],
        "edges": [
            {"source": "A", "target": "B", "strength": 0.8},
            {"source": "B", "target": "C", "strength": 0.6},
        ],
    }


def pair_config(*, direction="one-way", strength_mode="explicit"):
    return {
        "mode": "pair",
        "source": "A",
        "target": "B",
        "pair_direction": direction,
        "strength_mode": strength_mode,
        "strength": 0.8,
        "gain": 0.2,
        "cap": 0.08,
        "minutes_per_step": 10,
        "nodes": [
            {"id": "A", "character": "furina", "trust": 0.9, "love": 1.8,
             "familiarity": 1.0},
            {"id": "B", "character": "columbina", "trust": 0.2, "love": 0.1,
             "familiarity": 0.1, "boundaries": ["保持距离"]},
        ],
        "edges": [],
    }


class ContagionDashboardTest(unittest.TestCase):

    def setUp(self):
        with server._contagion_lock:
            server._contagion_state.update({
                "signature": None,
                "config": None,
                "nodes": {},
                "engines": {},
                "node_order": (),
                "mode": "network",
                "pair": None,
                "pair_bidirectional": False,
                "network": None,
                "source_id": None,
                "target_id": None,
                "turn": 0,
                "last_now": 0.0,
                "minutes_per_step": 10.0,
                "initial": {},
                "last_results": {},
                "history": {},
            })

    def tearDown(self):
        with server._contagion_lock:
            server._contagion_state.update({
                "signature": None,
                "config": None,
                "nodes": {},
                "engines": {},
                "node_order": (),
                "pair": None,
                "network": None,
                "initial": {},
                "last_results": {},
                "history": {},
            })

    def test_network_tick_matches_direct_contagion_network(self):
        config = network_config()
        appraisals = {"A": NEGATIVE, "B": NEUTRAL, "C": NEUTRAL}
        with server._contagion_lock:
            server._build_contagion_state(config)
            direct = ContagionNetwork(
                server._contagion_state["engines"],
                [
                    ContagionEdge("A", "B", 0.8),
                    ContagionEdge("B", "C", 0.6),
                ],
                gain=0.2, cap=0.08, hop_decay=0.5, max_hops=3,
            )
            expected = direct.derive(appraisals)
            payload = server._run_contagion_tick(config, appraisals, 1)

        self.assertEqual(payload["turn"], 1)
        self.assertFalse(payload["rebuilt"])
        for node_id, bias in expected.items():
            for dimension, value in bias.items():
                self.assertAlmostEqual(
                    payload["nodes"][node_id]["contagion_bias"][dimension],
                    value,
                    places=6,
                )

    def test_tick_advances_turn_and_history(self):
        config = network_config()
        appraisals = {"A": NEGATIVE, "B": NEUTRAL, "C": NEUTRAL}
        with server._contagion_lock:
            payload = server._run_contagion_tick(config, appraisals, 2)

        self.assertEqual(payload["turn"], 2)
        for node_id in payload["node_order"]:
            history = payload["nodes"][node_id]["history"]
            self.assertEqual([point["turn"] for point in history], [1, 2])

    def test_same_config_does_not_rebuild_but_changed_config_does(self):
        config = network_config()
        appraisals = {"A": NEGATIVE, "B": NEUTRAL, "C": NEUTRAL}
        with server._contagion_lock:
            first = server._run_contagion_tick(config, appraisals, 1)
            second = server._run_contagion_tick(config, appraisals, 1)
            changed = dict(config, gain=0.3)
            third = server._run_contagion_tick(changed, appraisals, 1)

        self.assertTrue(first["rebuilt"])
        self.assertFalse(second["rebuilt"])
        self.assertEqual(second["turn"], 2)
        self.assertTrue(third["rebuilt"])
        self.assertEqual(third["turn"], 1)

    def test_pair_one_way_only_target_receives_bias(self):
        config = pair_config()
        with server._contagion_lock:
            payload = server._contagion_reset(config)
            self.assertEqual(payload["strengths"], {"A->B": 0.8})
            payload = server._run_contagion_tick(
                config, {"A": NEGATIVE, "B": NEUTRAL}, 1,
            )

        self.assertEqual(payload["nodes"]["A"]["contagion_bias"], {})
        self.assertTrue(payload["nodes"]["B"]["contagion_bias"])

    def test_pair_bidirectional_uses_both_appraisals(self):
        config = pair_config(direction="bidirectional")
        with server._contagion_lock:
            payload = server._run_contagion_tick(
                config, {"A": NEGATIVE, "B": POSITIVE}, 1,
            )

        self.assertTrue(payload["pair_bidirectional"])
        self.assertIn("A->B", payload["strengths"])
        self.assertIn("B->A", payload["strengths"])
        self.assertTrue(payload["nodes"]["A"]["contagion_bias"])
        self.assertTrue(payload["nodes"]["B"]["contagion_bias"])

    def test_directional_pair_has_independent_strengths(self):
        config = pair_config(
            direction="bidirectional", strength_mode="directional",
        )
        with server._contagion_lock:
            payload = server._contagion_reset(config)

        strengths = payload["strengths"]
        self.assertGreater(strengths["A->B"], strengths["B->A"])
        self.assertAlmostEqual(strengths["A->B"], 0.916667, places=6)
        self.assertAlmostEqual(strengths["B->A"], 0.106667, places=6)

    def test_pair_auto_strength_reads_engine_state(self):
        config = pair_config(strength_mode="auto")
        with server._contagion_lock:
            payload = server._contagion_reset(config)

        source_strength = relationship_strength(0.9, 1.8)
        target_strength = relationship_strength(0.2, 0.1)
        expected = (source_strength + target_strength) / 2
        self.assertAlmostEqual(
            payload["strengths"]["A->B"], expected, places=6,
        )

    def test_nodes_use_isolated_state_memory_and_scars(self):
        with server._contagion_lock:
            server._build_contagion_state(network_config())
            engines = list(server._contagion_state["engines"].values())
        self.assertIsNot(engines[0].state, engines[1].state)
        self.assertIsNot(engines[0].memory, engines[1].memory)
        self.assertIsNot(engines[0].scars, engines[1].scars)
        self.assertIsNot(engines[0].tolerance, engines[1].tolerance)
        self.assertAlmostEqual(engines[0].state.trust, 0.8)
        self.assertAlmostEqual(engines[0].state.love, 1.2)

    def test_cap_is_applied_to_transmitted_bias(self):
        config = network_config()
        config["cap"] = 0.02
        appraisals = {"A": NEGATIVE, "B": NEUTRAL, "C": NEUTRAL}
        with server._contagion_lock:
            payload = server._run_contagion_tick(config, appraisals, 1)

        for bias in payload["nodes"]["B"]["contagion_bias"].values():
            self.assertLessEqual(abs(bias), 0.0200001)

    def test_payload_contains_render_fields(self):
        config = network_config()
        with server._contagion_lock:
            payload = server._contagion_reset(config)

        self.assertTrue(payload["initialized"])
        self.assertEqual(payload["node_order"], ["A", "B", "C"])
        for node_id in payload["node_order"]:
            node = payload["nodes"][node_id]
            for key in ("id", "name", "character", "dominant", "raw",
                        "felt", "initial", "delta", "contagion_bias",
                        "history"):
                self.assertIn(key, node)
            self.assertEqual(node["history"], [])

    def test_unknown_appraisal_node_is_rejected(self):
        config = network_config()
        with server._contagion_lock:
            with self.assertRaises(ValueError):
                server._run_contagion_tick(
                    config, {"A": NEGATIVE, "Z": NEUTRAL}, 1,
                )

    def test_invalid_configs_are_rejected(self):
        with server._contagion_lock:
            with self.assertRaises(ValueError):
                server._build_contagion_state(
                    {"mode": "network", "nodes": [
                        {"id": "A", "character": "default"},
                    ]},
                )
            with self.assertRaises(ValueError):
                server._build_contagion_state({
                    "mode": "network",
                    "nodes": [
                        {"id": "A", "character": "default"},
                        {"id": "A", "character": "kokomi"},
                    ],
                })
            with self.assertRaises(ValueError):
                server._build_contagion_state({
                    "mode": "pair",
                    "nodes": [
                        {"id": "A", "character": "default"},
                        {"id": "B", "character": "kokomi"},
                    ],
                    "source": "A",
                    "target": "A",
                })

    def test_edge_duplicate_and_self_loop_are_rejected(self):
        base = network_config()
        for edges in (
            [{"source": "A", "target": "B", "strength": 0.5},
             {"source": "A", "target": "B", "strength": 0.8}],
            [{"source": "A", "target": "A", "strength": 0.5}],
        ):
            config = dict(base, edges=edges)
            with server._contagion_lock:
                with self.assertRaises(ValueError):
                    server._build_contagion_state(config)


if __name__ == "__main__":
    unittest.main()
