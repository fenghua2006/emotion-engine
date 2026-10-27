"""L7.3：多角色有向感染网络、传播链与距离衰减测试。"""

import unittest

from contagion import (
    ContagionEdge,
    ContagionNetwork,
    ContagionPair,
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


def neutral_appraisal() -> Appraisal:
    return Appraisal(goal_relevance=0.8)


class NetworkDeriveTest(unittest.TestCase):
    def test_direct_edge_matches_pair(self):
        source = make_engine()
        target = make_engine()
        pair = ContagionPair(source, target)
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
            [ContagionEdge("A", "B")],
        )

        expected = pair.derive(negative_appraisal())
        self.assertEqual(
            network.derive({"A": negative_appraisal()}),
            {"B": expected},
        )

    def test_no_edges_produces_no_bias(self):
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
        )
        self.assertEqual(network.derive({"A": negative_appraisal()}), {})

    def test_neutral_or_missing_appraisals_produce_no_bias(self):
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
            [ContagionEdge("A", "B")],
        )
        self.assertEqual(network.derive({}), {})
        self.assertEqual(
            network.derive({"A": neutral_appraisal()}),
            {},
        )
        self.assertEqual(
            network.derive({"A": None, "B": positive_appraisal()}),
            {},
        )

    def test_two_hop_chain_attenuates(self):
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine(), "C": make_engine()},
            [
                ContagionEdge("A", "B", 0.8),
                ContagionEdge("B", "C", 0.5),
            ],
            hop_decay=0.5,
        )
        bias = network.derive({"A": negative_appraisal()})

        self.assertAlmostEqual(
            bias["B"]["goal_conduciveness"], -0.064,
        )
        # -0.08 * 0.8 * 0.5 * 0.5
        self.assertAlmostEqual(
            bias["C"]["goal_conduciveness"], -0.016,
        )
        self.assertLess(
            abs(bias["C"]["goal_conduciveness"]),
            abs(bias["B"]["goal_conduciveness"]),
        )

    def test_hop_decay_zero_stops_after_direct_edge(self):
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine(), "C": make_engine()},
            [
                ContagionEdge("A", "B", 1.0),
                ContagionEdge("B", "C", 1.0),
            ],
            hop_decay=0.0,
        )
        bias = network.derive({"A": negative_appraisal()})
        self.assertIn("B", bias)
        self.assertNotIn("C", bias)

    def test_max_hops_limits_chain(self):
        edges = [
            ContagionEdge("A", "B", 1.0),
            ContagionEdge("B", "C", 1.0),
            ContagionEdge("C", "D", 1.0),
        ]
        engines = {
            name: make_engine() for name in ("A", "B", "C", "D")
        }
        two_hop = ContagionNetwork(
            engines, edges, hop_decay=1.0, max_hops=2,
        )
        bias = two_hop.derive({"A": negative_appraisal()})
        self.assertIn("C", bias)
        self.assertNotIn("D", bias)

        three_hop = ContagionNetwork(
            {name: make_engine() for name in ("A", "B", "C", "D")},
            edges, hop_decay=1.0, max_hops=3,
        )
        self.assertIn(
            "D", three_hop.derive({"A": negative_appraisal()}),
        )

    def test_multiple_sources_sum_then_clamp(self):
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine(), "C": make_engine()},
            [
                ContagionEdge("A", "B", 1.0),
                ContagionEdge("C", "B", 1.0),
            ],
            gain=1.0,
            cap=0.05,
        )
        bias = network.derive({
            "A": negative_appraisal(),
            "C": negative_appraisal(),
        })
        for value in bias["B"].values():
            self.assertAlmostEqual(value, -0.05)

    def test_cycle_does_not_amplify_or_self_infect(self):
        pair = ContagionPair(make_engine(), make_engine())
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
            [
                ContagionEdge("A", "B", 1.0),
                ContagionEdge("B", "A", 1.0),
            ],
        )
        bias = network.derive({
            "A": negative_appraisal(),
            "B": positive_appraisal(),
        })
        self.assertEqual(bias["A"], pair.derive_reverse(positive_appraisal()))
        self.assertEqual(bias["B"], pair.derive(negative_appraisal()))

    def test_edge_order_is_irrelevant(self):
        forward = ContagionNetwork(
            {"A": make_engine(), "B": make_engine(), "C": make_engine()},
            [
                ContagionEdge("A", "B", 0.7),
                ContagionEdge("B", "C", 0.4),
                ContagionEdge("A", "C", 0.3),
            ],
        )
        reverse = ContagionNetwork(
            {"A": make_engine(), "B": make_engine(), "C": make_engine()},
            [
                ContagionEdge("A", "C", 0.3),
                ContagionEdge("B", "C", 0.4),
                ContagionEdge("A", "B", 0.7),
            ],
        )
        appraisals = {"A": negative_appraisal()}
        self.assertEqual(forward.derive(appraisals), reverse.derive(appraisals))

    def test_unknown_appraisal_keys_are_ignored(self):
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
            [ContagionEdge("A", "B")],
        )
        bias = network.derive({
            "A": negative_appraisal(),
            "unknown": negative_appraisal(),
        })
        self.assertEqual(set(bias), {"B"})

    def test_edge_strength_is_normalized(self):
        self.assertEqual(ContagionEdge("A", "B", 2.0).strength, 1.0)
        self.assertEqual(ContagionEdge("A", "B", -1.0).strength, 0.0)
        self.assertEqual(ContagionEdge("A", "B", None).strength, 1.0)

    def test_zero_strength_edge_produces_no_bias(self):
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
            [ContagionEdge("A", "B", 0.0)],
        )
        self.assertEqual(
            network.derive({"A": negative_appraisal()}), {},
        )

    def test_network_level_parameters_are_normalized(self):
        engines = {"A": make_engine(), "B": make_engine()}
        network = ContagionNetwork(
            engines, [], hop_decay=2.0, max_hops=0,
        )
        self.assertEqual(network.hop_decay, 1.0)
        self.assertEqual(network.max_hops, 1)

        fallback = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
            [], hop_decay=float("nan"), max_hops=100,
        )
        self.assertEqual(fallback.hop_decay, 0.5)
        self.assertEqual(fallback.max_hops, 8)


class NetworkValidationTest(unittest.TestCase):
    def test_empty_engines_rejected(self):
        with self.assertRaises(ValueError):
            ContagionNetwork({})

    def test_duplicate_engine_rejected(self):
        engine = make_engine()
        with self.assertRaises(ValueError):
            ContagionNetwork({"A": engine, "B": engine})

    def test_shared_state_rejected(self):
        source = make_engine()
        target = make_engine()
        target.state = source.state
        with self.assertRaises(ValueError):
            ContagionNetwork({"A": source, "B": target})

    def test_bad_edges_rejected(self):
        engines = {"A": make_engine(), "B": make_engine()}
        cases = [
            ([("A", "A")], ValueError),
            ([ContagionEdge("A", "missing")], ValueError),
            ([("A", "B"), ("A", "B")], ValueError),
            ([("A", "B", 0.5, "extra")], ValueError),
            (["A->B"], TypeError),
            ([object()], TypeError),
        ]
        for edges, error in cases:
            with self.subTest(edges=edges):
                with self.assertRaises(error):
                    ContagionNetwork(engines, edges)

    def test_non_engine_rejected(self):
        with self.assertRaises(TypeError):
            ContagionNetwork({"A": object(), "B": make_engine()})


class NetworkTickTest(unittest.TestCase):
    def test_tick_matches_manual_tick_with_contagion(self):
        appraisals = {"A": negative_appraisal(), "B": positive_appraisal()}
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
            [ContagionEdge("A", "B", 0.5)],
        )
        biases = network.derive(appraisals)
        results = network.tick(
            appraisals,
            boundary_violations={"B": True},
            appraisal_biases={"B": {"expectedness": 0.03}},
            now=NOW,
            trace=True,
        )

        expected_a = make_engine().tick(
            appraisals["A"], now=NOW, trace=True,
        )
        expected_b = make_engine().tick(
            appraisals["B"],
            boundary_violation=True,
            contagion_bias=biases["B"],
            appraisal_bias={"expectedness": 0.03},
            now=NOW,
            trace=True,
        )
        self.assertEqual(results["A"], expected_a)
        self.assertEqual(results["B"], expected_b)

    def test_tick_without_appraisals_is_plain(self):
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
            [ContagionEdge("A", "B")],
        )
        results = network.tick(now=NOW, trace=True)
        for name in ("A", "B"):
            self.assertEqual(
                results[name],
                make_engine().tick(None, now=NOW, trace=True),
            )
            self.assertNotIn("contagion_bias", results[name])

    def test_tick_preserves_node_order(self):
        network = ContagionNetwork(
            {"B": make_engine(), "A": make_engine()},
            [ContagionEdge("A", "B")],
        )
        results = network.tick(
            {"A": negative_appraisal(), "B": positive_appraisal()},
            now=NOW,
        )
        self.assertEqual(list(results), ["B", "A"])

    def test_infected_trace_adds_three_keys(self):
        network = ContagionNetwork(
            {"A": make_engine(), "B": make_engine()},
            [ContagionEdge("A", "B")],
        )
        results = network.tick(
            {"A": negative_appraisal(), "B": positive_appraisal()},
            now=NOW,
            trace=True,
        )
        plain = make_engine().tick(
            positive_appraisal(), now=NOW, trace=True,
        )
        added = set(results["B"]["trace"]) - set(plain["trace"])
        self.assertEqual(
            added,
            {
                "contagion_enabled",
                "contagion_applied",
                "appraisal_before_contagion",
            },
        )


if __name__ == "__main__":
    unittest.main()
