"""L8.5 主动联系的 `/api/scene/proactive` 服务端测试。"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from dashboard import server


def proactive_config(**overrides):
    values = {
        "min_silence_seconds": 0,
        "silence_window_seconds": 10800,
        "cooldown_seconds": 0,
        "max_per_day": 5,
        "max_unanswered": 5,
        "quiet_start_hour": None,
        "quiet_end_hour": None,
        "threshold": 0.0,
    }
    values.update(overrides)
    return values


def scene_config(**proactive):
    config = server._scene_default_config()
    config["proactive"] = proactive_config(**proactive)
    return config


def make_actor_eager(actor_id="B"):
    scene = server._scene_state["scene"]
    state = scene.actors[actor_id].engine.state
    state.longing = 1.0
    state.love = 1.0
    state.trust = 0.9


class ProactiveDashboardTest(unittest.TestCase):

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

    def test_default_config_contains_proactive_defaults(self):
        config = server._scene_config(server._scene_default_config())
        proactive = config["proactive"]
        self.assertEqual(proactive["min_silence_seconds"], 1800.0)
        self.assertEqual(proactive["silence_window_seconds"], 10800.0)
        self.assertEqual(proactive["max_per_day"], 2)
        self.assertEqual(proactive["quiet_start_hour"], 23)
        self.assertEqual(proactive["threshold"], 0.35)

    def test_proactive_config_is_normalized(self):
        config = server._scene_config({
            "proactive": {
                "threshold": 2,
                "max_per_day": 99,
                "max_unanswered": -3,
                "quiet_start_hour": None,
                "quiet_end_hour": None,
                "min_silence_seconds": -10,
            },
        })
        proactive = config["proactive"]
        self.assertEqual(proactive["threshold"], 1.0)
        self.assertEqual(proactive["max_per_day"], 20)
        self.assertEqual(proactive["max_unanswered"], 0)
        self.assertIsNone(proactive["quiet_start_hour"])
        self.assertEqual(proactive["min_silence_seconds"], 0.0)

    def test_check_returns_blocked_candidates(self):
        config = scene_config(min_silence_seconds=1800)
        with server._scene_lock:
            server._scene_reset(config)
            payload = server._scene_proactive({
                "action": "check", "actor_id": "A",
            })

        self.assertEqual(payload["action"], "check")
        self.assertIsNone(payload["chosen"])
        self.assertEqual(set(payload["candidates"][0]), {
            "actor_id", "target_id", "urgency", "eligible", "blocked_reason",
            "reasons", "components", "elapsed_seconds", "pending",
        })
        for item in payload["candidates"]:
            self.assertFalse(item["eligible"])
            self.assertEqual(item["blocked_reason"], "min_silence")
        self.assertIn("config", payload["proactive"])

    def test_check_chooses_highest_eligible_actor(self):
        now = time.time() + 60
        with server._scene_lock:
            server._scene_reset(scene_config())
            make_actor_eager("B")
            payload = server._scene_proactive({
                "action": "check", "now": now,
            })

        self.assertEqual(payload["chosen"]["actor_id"], "B")
        self.assertTrue(payload["chosen"]["eligible"])
        eligible = [
            item for item in payload["candidates"] if item["eligible"]
        ]
        self.assertEqual(len(eligible), 3)
        self.assertEqual(
            max(eligible, key=lambda item: item["urgency"]),
            payload["chosen"],
        )

    def test_send_creates_turn_and_marks_contact(self):
        now = time.time() + 60
        with server._scene_lock:
            server._scene_reset(scene_config())
            make_actor_eager("B")
            payload = server._scene_proactive({
                "action": "send", "now": now,
            })
            log = list(server._scene_state["turn_log"])
            expected_name = server._scene_state["scene"].actors["B"].name

        self.assertTrue(payload["sent"])
        self.assertEqual(payload["turn"], 1)
        self.assertEqual(payload["source"], "B")
        self.assertTrue(payload["text"])
        self.assertEqual(payload["proactive_send"]["actor_id"], "B")
        self.assertEqual(payload["proactive_send"]["name"], expected_name)
        self.assertEqual(
            payload["proactive_send"]["receipt"]["pending"], 1,
        )
        self.assertEqual(
            payload["proactive_send"]["snapshot"]["pending"]["B"], 1,
        )
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["source"], "B")
        self.assertEqual(log[0]["text"], payload["text"])
        self.assertIn("A", payload["relationship_changes"])
        self.assertIn("C", payload["relationship_changes"])
        self.assertAlmostEqual(
            payload["scene"]["relationships"]["A->B"]["interactions"],
            1.0,
        )

    def test_send_can_force_a_specific_eligible_actor(self):
        now = time.time() + 60
        with server._scene_lock:
            server._scene_reset(scene_config())
            payload = server._scene_proactive({
                "action": "send", "actor_id": "C", "now": now,
            })

        self.assertTrue(payload["sent"])
        self.assertEqual(payload["source"], "C")
        self.assertEqual(payload["proactive_send"]["actor_id"], "C")

    def test_send_with_blocked_actor_raises(self):
        with server._scene_lock:
            server._scene_reset(scene_config(min_silence_seconds=1800))
            with self.assertRaises(ValueError) as caught:
                server._scene_proactive({
                    "action": "send",
                    "actor_id": "A",
                    "now": time.time() + 60,
                })
        self.assertIn("min_silence", str(caught.exception))

    def test_unknown_action_and_actor_are_rejected(self):
        with server._scene_lock:
            server._scene_reset(scene_config())
            with self.assertRaises(ValueError):
                server._scene_proactive({"action": "dance"})
            with self.assertRaises(ValueError):
                server._scene_proactive({
                    "action": "check", "actor_id": "Z",
                })

    def test_user_turn_clears_pending_proactive_count(self):
        now = time.time() + 60
        with server._scene_lock:
            server._scene_reset(scene_config())
            make_actor_eager("B")
            server._scene_proactive({"action": "send", "now": now})
            turn = server._scene_turn({
                "speaker_id": "user",
                "text": "我回来了。",
                "now": now + 10,
            })
            snapshot = turn["scene"]["proactive"]

        self.assertEqual(snapshot["pending"]["B"], 0)
        self.assertEqual(snapshot["last_speaker"], "user")
        self.assertIsNotNone(snapshot["last_user_at"])

    def test_cooldown_blocks_second_contact(self):
        now = time.time() + 60
        with server._scene_lock:
            server._scene_reset(scene_config(cooldown_seconds=21600))
            make_actor_eager("B")
            first = server._scene_proactive({
                "action": "send", "actor_id": "B", "now": now,
            })
            self.assertTrue(first["sent"])
            with self.assertRaises(ValueError) as caught:
                server._scene_proactive({
                    "action": "send",
                    "actor_id": "B",
                    "now": now + 60,
                })
        self.assertIn("cooldown", str(caught.exception))

    def test_check_does_not_tick_or_update_relationships(self):
        now = time.time() + 60
        with server._scene_lock:
            server._scene_reset(scene_config())
            scene = server._scene_state["scene"]
            scene.set_relationship("A", "B", trust=0.31)
            before_state = scene.actors["B"].engine.state.to_dict()
            before_update = scene.actors["B"].engine.state._last_update
            before_rel = scene.relationship("A", "B").to_dict()
            payload = server._scene_proactive({
                "action": "check", "now": now,
            })
            after_state = scene.actors["B"].engine.state.to_dict()
            after_rel = scene.relationship("A", "B").to_dict()

        self.assertIsNotNone(payload["chosen"])
        self.assertEqual(after_state, before_state)
        self.assertEqual(
            scene.actors["B"].engine.state._last_update, before_update,
        )
        self.assertEqual(after_rel, before_rel)
        self.assertEqual(payload["chosen"]["pending"], 0)

    def test_reset_clears_proactive_counters_but_keeps_rules(self):
        now = time.time() + 60
        with server._scene_lock:
            server._scene_reset(scene_config(threshold=0.42))
            make_actor_eager("B")
            server._scene_proactive({"action": "send", "now": now})
            payload = server._scene_reset(scene_config(threshold=0.42))

        proactive = payload["proactive"]
        self.assertEqual(proactive["total_contacts"], 0)
        self.assertEqual(proactive["pending"], {"A": 0, "B": 0, "C": 0})
        self.assertEqual(proactive["config"]["threshold"], 0.42)

    def test_scene_page_exposes_proactive_controls(self):
        html = Path(server.SCENE_HTML_PATH).read_text(encoding="utf-8")
        self.assertIn("/api/scene/proactive", html)
        self.assertIn("proactive-check", html)
        self.assertIn("proactive-send", html)
        self.assertIn("主动联系", html)


if __name__ == "__main__":
    unittest.main()
