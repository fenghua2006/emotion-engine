"""仪表盘持久记忆隔离测试。

目标：
  - 单角色仪表盘不再使用全局 MemoryStoreDB；
  - 所有持久记忆走 RelationshipMemoryStore，按 (character_id, user_id) 隔离；
  - 未保存的 custom 角色按显示名拆键，不共用 "custom" 记忆域；
  - /api/event 的本地模板路径也会写入关系域记忆。
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

from dashboard import server
from engine import (
    Appraisal, EmotionalState, EmotionEngine, MemoryStore, Personality,
    SensitizationStore,
)
from memory import MemoryService, RelationshipMemoryStore


def make_engine() -> EmotionEngine:
    return EmotionEngine(
        state=EmotionalState(),
        personality=Personality(),
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


class DashboardMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "memory.db")
        self.service = MemoryService(RelationshipMemoryStore(self.db_path))
        self.old_state = dict(server._state)
        self.engine = make_engine()
        server._state.update({
            "engine": self.engine,
            "appraise_fn": None,
            "char_id": "furina",
            "char_name": "芙宁娜",
            "memory_id": "furina",
            "user_id": "alice",
            "memory_service": self.service,
            "memory_db_path": self.db_path,
            "memory_last_error": "",
            "history": [],
            "custom_desc": "",
        })

    def tearDown(self):
        server._state.clear()
        server._state.update(self.old_state)
        self.service.store.close()
        self.tmp.cleanup()

    def test_unsaved_custom_characters_get_distinct_memory_keys(self):
        furina = server._memory_character_id("custom", "芙宁娜")
        kokomi = server._memory_character_id("custom", "心海")
        self.assertNotEqual(furina, kokomi)
        self.assertEqual(furina, server._memory_character_id("custom", "芙宁娜"))
        self.assertEqual(
            server._memory_character_id("saved_oc", "任意名字"),
            "saved_oc",
        )

    def test_dashboard_no_longer_constructs_global_memory_store_db(self):
        source = Path(server.__file__).with_name("server.py").read_text(
            encoding="utf-8",
        )
        self.assertNotIn("MemoryStoreDB()", source)

    def test_remember_turn_is_scoped_by_character(self):
        server._state["memory_id"] = "furina"
        server._remember_turn(
            self.engine, "我叫小枫。", "记住了。",
            Appraisal(goal_relevance=0.6, goal_conduciveness=0.2),
        )
        server._state["memory_id"] = "kokomi"
        server._remember_turn(
            self.engine, "我叫心海。", "好的。",
            Appraisal(goal_relevance=0.6, goal_conduciveness=0.2),
        )

        furina_context = self.service.build_context(
            "furina", "alice", "小枫",
        )
        kokomi_context = self.service.build_context(
            "kokomi", "alice", "心海",
        )
        self.assertIn("小枫", furina_context)
        self.assertNotIn("心海", furina_context)
        self.assertIn("心海", kokomi_context)
        self.assertNotIn("小枫", kokomi_context)
        self.assertEqual(
            self.service.store.stats("furina", "alice")["facts"], 1,
        )
        self.assertEqual(
            self.service.store.stats("kokomi", "alice")["facts"], 1,
        )

    def test_same_character_isolated_by_user(self):
        server._state["memory_id"] = "furina"
        server._state["user_id"] = "alice"
        server._remember_turn(
            self.engine, "我叫小枫。", "记住了。",
            Appraisal(goal_relevance=0.6, goal_conduciveness=0.2),
        )
        server._state["user_id"] = "bob"
        server._remember_turn(
            self.engine, "我叫小海。", "记住了。",
            Appraisal(goal_relevance=0.6, goal_conduciveness=0.2),
        )

        alice_context = self.service.build_context(
            "furina", "alice", "小枫",
        )
        bob_context = self.service.build_context(
            "furina", "bob", "小海",
        )
        self.assertIn("小枫", alice_context)
        self.assertNotIn("小海", alice_context)
        self.assertIn("小海", bob_context)
        self.assertNotIn("小枫", bob_context)

    def test_memory_prior_and_stats_are_scoped(self):
        self.service.remember_event(
            "furina", "alice", "小枫说过喜欢被温柔对待。",
            appraisal=Appraisal(
                goal_relevance=0.7, goal_conduciveness=0.6,
                expectedness=0.4, social_evaluation=0.8,
            ),
            salience=1.0,
        )
        server._state["memory_id"] = "furina"
        furina_prior = server._memory_appraisal_prior()
        server._state["memory_id"] = "kokomi"
        kokomi_prior = server._memory_appraisal_prior()

        self.assertTrue(furina_prior)
        self.assertNotEqual(furina_prior, kokomi_prior)
        state = server.build_state_response()
        self.assertEqual(state["memory_scope"]["character_id"], "kokomi")
        self.assertEqual(state["memory_scope"]["user_id"], "alice")
        self.assertEqual(state["memory"]["relationship"]["cards"], 0)
        self.assertEqual(state["memory"]["flash"], 0)

    def test_local_event_path_writes_relationship_memory(self):
        # 在测试主线程里处理请求，确保 SQLite 连接与请求在同一线程；
        # 客户端线程只负责发起 HTTP，不碰存储。
        httpd = HTTPServer(("127.0.0.1", 0), server.DashboardHandler)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def post_event(holder):
            request = urllib.request.Request(
                base + "/api/event",
                data=json.dumps({"text": holder["text"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as resp:
                holder["payload"] = json.loads(resp.read().decode("utf-8"))

        try:
            with patch.object(server, "save_to_disk", lambda: None), \
                 patch.object(server, "save_chat_message", lambda *a: None):
                for char_id, name, text in (
                    ("furina", "芙宁娜", "我叫小枫。"),
                    ("kokomi", "心海", "我叫心海。"),
                ):
                    server._state["memory_id"] = char_id
                    server._state["char_id"] = char_id
                    server._state["char_name"] = name
                    holder = {"text": text}
                    client = threading.Thread(
                        target=post_event, args=(holder,), daemon=True,
                    )
                    client.start()
                    httpd.handle_request()
                    client.join(timeout=5)
                    self.assertIn("state", holder.get("payload", {}))
        finally:
            httpd.server_close()

        furina_context = self.service.build_context(
            "furina", "alice", "小枫",
        )
        kokomi_context = self.service.build_context(
            "kokomi", "alice", "心海",
        )
        self.assertIn("小枫", furina_context)
        self.assertNotIn("心海", furina_context)
        self.assertIn("心海", kokomi_context)
        self.assertNotIn("小枫", kokomi_context)


if __name__ == "__main__":
    unittest.main()
