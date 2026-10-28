"""L8 朋友圈网页实验台的服务器端测试。

不启动 HTTP 服务，直接验证配置解析、场景重建、回合编排、关系持久化
与返回给前端的 payload；HTML 只做存在性和关键接口的冒烟检查。
"""

import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

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


def scene_config():
    return server._scene_default_config()


class SceneDashboardTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with server._scene_lock:
            server._close_scene()
            server._scene_state.update({
                "signature": None,
                "config": None,
                "scene": None,
                "store": None,
                "db_path": os.path.join(self.tmp.name, "social.db"),
                "turn_log": [],
                "last_payload": None,
            })

    def tearDown(self):
        with server._scene_lock:
            server._close_scene()
            server._scene_state.update({
                "signature": None,
                "config": None,
                "scene": None,
                "store": None,
                "db_path": server.DEFAULT_SOCIAL_DB_PATH,
                "turn_log": [],
                "last_payload": None,
            })
        self.tmp.cleanup()

    def test_reset_returns_initialized_scene_payload(self):
        with server._scene_lock:
            payload = server._scene_reset(scene_config())

        self.assertTrue(payload["initialized"])
        self.assertEqual(payload["world_id"], "dashboard")
        self.assertEqual(payload["turn"], 0)
        self.assertEqual(tuple(payload["actor_order"]), ("A", "B", "C"))
        self.assertEqual(payload["config"]["gain"], 0.2)
        self.assertEqual(payload["turn_log"], [])
        self.assertEqual(set(payload["actors"]), {"A", "B", "C"})
        for source in ("A", "B", "C"):
            for target in ("A", "B", "C"):
                if source == target:
                    continue
                self.assertIn(f"{source}->{target}", payload["relationships"])
        self.assertTrue(payload["edges"])

    def test_actor_turn_produces_bias_and_utterances(self):
        config = scene_config()
        with server._scene_lock:
            server._scene_reset(config)
            payload = server._scene_turn({
                "speaker_id": "A",
                "text": "我今天真的很难过。",
                "appraisal": NEGATIVE,
            })

        self.assertEqual(payload["turn"], 1)
        self.assertEqual(payload["source"], "A")
        self.assertEqual(set(payload["biases"]), {"B", "C"})
        self.assertEqual(payload["actors"]["A"]["contagion_bias"], {})
        self.assertTrue(payload["actors"]["B"]["contagion_bias"])
        self.assertTrue(payload["actors"]["C"]["contagion_bias"])
        for actor in payload["actors"].values():
            self.assertTrue(actor["utterance"])
            self.assertIn("felt", actor)
            self.assertIn("zone", actor)
        self.assertIn("B", payload["relationship_changes"])
        self.assertIn("C", payload["relationship_changes"])

    def test_user_turn_does_not_update_peer_relationships(self):
        config = scene_config()
        with server._scene_lock:
            server._scene_reset(config)
            before = server._scene_payload()["relationships"]["B->A"]
            payload = server._scene_turn({
                "speaker_id": "user",
                "text": "大家早上好。",
                "appraisal": POSITIVE,
            })
            after = server._scene_payload()["relationships"]["B->A"]

        self.assertEqual(payload["turn"], 1)
        self.assertEqual(payload["biases"], {})
        self.assertEqual(payload["relationship_changes"], {})
        self.assertEqual(before["trust"], after["trust"])
        self.assertEqual(before["tension"], after["tension"])
        self.assertEqual(before["interactions"], after["interactions"])

    def test_relationship_updates_are_directional_and_persisted(self):
        config = scene_config()
        with server._scene_lock:
            server._scene_reset(config)
            payload = server._scene_turn({
                "speaker_id": "A",
                "text": "谢谢你一直听我说。",
                "appraisal": POSITIVE,
            })
            b_to_a = server._scene_state["scene"].relationship("B", "A")
            a_to_b = server._scene_state["scene"].relationship("A", "B")

        self.assertGreater(b_to_a.trust, 0.2)
        self.assertGreater(b_to_a.familiarity, 0.0)
        self.assertEqual(b_to_a.interactions, 1)
        self.assertAlmostEqual(a_to_b.trust, 0.2)
        self.assertEqual(payload["relationship_changes"]["B"]["source_id"], "A")

    def test_negative_turn_raises_tension(self):
        config = scene_config()
        with server._scene_lock:
            server._scene_reset(config)
            server._scene_turn({
                "speaker_id": "A",
                "text": "你怎么能这样说。",
                "appraisal": NEGATIVE,
            })
            b_to_a = server._scene_state["scene"].relationship("B", "A")

        self.assertLess(b_to_a.trust, 0.2)
        self.assertGreater(b_to_a.tension, 0.0)

    def test_rebuild_keeps_relationships_unless_explicitly_reset(self):
        config = scene_config()
        with server._scene_lock:
            server._scene_reset(config)
            server._scene_turn({
                "speaker_id": "A",
                "text": "谢谢。",
                "appraisal": POSITIVE,
            })
            kept = server._scene_reset(config)
            cleared = server._scene_reset(config, reset_relationships=True)

        self.assertGreater(kept["relationships"]["B->A"]["trust"], 0.2)
        self.assertEqual(kept["turn"], 0)
        self.assertEqual(kept["turn_log"], [])
        self.assertEqual(cleared["relationships"]["B->A"]["trust"], 0.2)
        self.assertEqual(cleared["relationships"]["B->A"]["interactions"], 0)

    def test_changed_config_rebuilds_and_clears_turn_log(self):
        config = scene_config()
        changed = dict(config, gain=0.35)
        with server._scene_lock:
            server._scene_reset(config)
            server._scene_turn({
                "speaker_id": "A",
                "text": "第一回合。",
                "appraisal": NEUTRAL,
            })
            payload = server._scene_turn({
                "config": changed,
                "speaker_id": "A",
                "text": "第二回合。",
                "appraisal": NEUTRAL,
            })
            state = server._scene_payload()

        self.assertEqual(payload["turn"], 1)
        self.assertEqual(payload["config"]["gain"], 0.35)
        self.assertEqual(len(state["turn_log"]), 1)
        self.assertEqual(state["turn_log"][0]["text"], "第二回合。")

    def test_scene_actors_keep_engines_and_resources_isolated(self):
        config = scene_config()
        with server._scene_lock:
            server._scene_reset(config)
            scene = server._scene_state["scene"]
            engines = [actor.engine for actor in scene.actors.values()]
            before = [asdict(engine.personality) for engine in engines]
            server._scene_turn({
                "speaker_id": "A",
                "text": "今天发生了很多事。",
                "appraisal": POSITIVE,
            })
            after = [asdict(actor.engine.personality)
                     for actor in scene.actors.values()]

        for index, engine in enumerate(engines):
            for other in engines[index + 1:]:
                self.assertIsNot(engine, other)
                self.assertIsNot(engine.state, other.state)
                self.assertIsNot(engine.memory, other.memory)
                self.assertIsNot(engine.scars, other.scars)
                self.assertIsNot(engine.tolerance, other.tolerance)
        self.assertEqual(before, after)

    def test_turn_log_is_capped(self):
        config = scene_config()
        original_limit = server.SCENE_HISTORY_LIMIT
        server.SCENE_HISTORY_LIMIT = 3
        try:
            with server._scene_lock:
                server._scene_reset(config)
                for index in range(5):
                    server._scene_turn({
                        "speaker_id": "A",
                        "text": f"第 {index} 句话。",
                        "appraisal": NEUTRAL,
                    })
                state = server._scene_payload()
        finally:
            server.SCENE_HISTORY_LIMIT = original_limit

        self.assertEqual(len(state["turn_log"]), 3)
        self.assertEqual(
            [entry["text"] for entry in state["turn_log"]],
            ["第 2 句话。", "第 3 句话。", "第 4 句话。"],
        )

    def test_invalid_inputs_are_rejected(self):
        config = scene_config()
        with server._scene_lock:
            server._scene_reset(config)
            with self.assertRaises(ValueError):
                server._scene_config({
                    "actors": [
                        {"id": "A", "character": "default"},
                        {"id": "A", "character": "default"},
                    ],
                })
            with self.assertRaises(ValueError):
                server._scene_turn({
                    "speaker_id": "A",
                    "text": "",
                    "appraisal": NEUTRAL,
                })
            with self.assertRaises(ValueError):
                server._scene_turn({
                    "speaker_id": "Z",
                    "text": "你好。",
                    "appraisal": NEUTRAL,
                })
            with self.assertRaises(ValueError):
                server._scene_turn({
                    "speaker_id": "A",
                    "text": "x" * (server.SCENE_MAX_TEXT + 1),
                    "appraisal": NEUTRAL,
                })

    def test_explicit_appraisal_is_clamped(self):
        with server._scene_lock:
            appraisal = server._scene_appraisal_from_payload({
                "appraisal": {
                    "goal_relevance": 5.0,
                    "goal_conduciveness": -9.0,
                    "social_evaluation": 9.0,
                },
            })

        self.assertEqual(appraisal.goal_relevance, 1.0)
        self.assertEqual(appraisal.goal_conduciveness, -1.0)
        self.assertEqual(appraisal.social_evaluation, 1.0)

    def test_relationships_survive_store_reopen(self):
        config = scene_config()
        with server._scene_lock:
            server._scene_reset(config)
            server._scene_turn({
                "speaker_id": "A",
                "text": "晚安。",
                "appraisal": POSITIVE,
            })
            expected = server._scene_state["scene"].relationship("B", "A").to_dict()
            server._close_scene()
            reopened = server._scene_reset(config)
            loaded = reopened["relationships"]["B->A"]

        self.assertAlmostEqual(loaded["trust"], expected["trust"])
        self.assertAlmostEqual(loaded["familiarity"], expected["familiarity"])
        self.assertEqual(loaded["interactions"], expected["interactions"])

    def test_scene_page_contains_api_endpoints(self):
        path = Path(server.SCENE_HTML_PATH)
        self.assertTrue(path.is_file(), "dashboard/scene.html is missing")
        html = path.read_text(encoding="utf-8")
        for endpoint in (
            "/api/scene/state",
            "/api/scene/reset",
            "/api/scene/turn",
        ):
            self.assertIn(endpoint, html)
        self.assertIn("朋友圈", html)


if __name__ == "__main__":
    unittest.main()
