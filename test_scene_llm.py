"""L8 朋友圈场景的可选 LLM 表达层测试。

默认关闭：`/scene` 仍只调用本地 `respond()`，返回键集合和台词生成路径不变。
开启后：每个角色先由引擎 tick，再由 `respond_llm()` 替换台词；API 失败时
保留本地模板。密钥只存在于服务端内存，不进入场景快照、回合响应或日志。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard import server
from proactive import ProactiveAssessment


NEGATIVE = {
    "goal_relevance": 0.8,
    "goal_conduciveness": -0.6,
    "expectedness": 0.2,
    "coping_potential": 0.2,
    "other_agency": 0.6,
    "social_evaluation": -0.4,
}


def llm_config(**overrides):
    config = server._scene_default_config()
    llm = dict(
        config["llm"],
        enabled=True,
        api_key="sk-main-secret",
        fallback_key="sk-fallback-secret",
    )
    llm.update(overrides)
    config["llm"] = llm
    return config


def fake_llm_result(engine, text, **kwargs):
    return {
        "utterance": f"LLM-{kwargs.get('character_name', '?')}",
        "_llm_used": True,
        "_llm_backend": "DeepSeek",
        "_dominant": "sadness",
        "_state": {},
        "action": "approach",
        "tolerance": {"zone": "regulated"},
        "contagion_bias": kwargs.get("contagion_bias") or {},
    }


class SceneLLMTest(unittest.TestCase):

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
                "llm": server._scene_llm_defaults(),
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
                "llm": server._scene_llm_defaults(),
            })
        self.tmp.cleanup()

    def test_default_scene_never_calls_llm(self):
        with mock.patch.object(
            server, "respond", wraps=server.respond,
        ) as local, mock.patch.object(server, "respond_llm") as remote:
            with server._scene_lock:
                payload = server._scene_reset(
                    server._scene_default_config(),
                )
                result = server._scene_turn({
                    "speaker_id": "A",
                    "text": "我今天真的很难过。",
                    "appraisal": NEGATIVE,
                })

        remote.assert_not_called()
        self.assertEqual(local.call_count, 3)
        self.assertFalse(payload["config"]["llm"]["enabled"])
        for actor in result["actors"].values():
            self.assertNotIn("llm_used", actor)
            self.assertNotIn("llm_backend", actor)
            self.assertNotIn("llm_error", actor)
            self.assertTrue(actor["utterance"])
        self.assertNotIn("llm", result)

    def test_enabled_scene_calls_llm_once_per_actor(self):
        with mock.patch.object(
            server, "respond_llm", side_effect=fake_llm_result,
        ) as remote:
            with server._scene_lock:
                server._scene_reset(llm_config())
                result = server._scene_turn({
                    "speaker_id": "A",
                    "text": "我今天真的很难过。",
                    "appraisal": NEGATIVE,
                })

        self.assertEqual(remote.call_count, 3)
        self.assertTrue(result["llm"]["enabled"])
        self.assertTrue(result["llm"]["has_api_key"])
        self.assertTrue(result["llm"]["has_fallback_key"])
        self.assertNotIn("api_key", result["llm"])
        self.assertNotIn("sk-main-secret", json.dumps(result))
        for actor in result["actors"].values():
            self.assertTrue(actor["llm_used"])
            self.assertEqual(actor["llm_backend"], "DeepSeek")
            self.assertEqual(actor["llm_error"], "")
            self.assertTrue(actor["utterance"].startswith("LLM-"))

        calls = {call.kwargs["character_id"]: call for call in remote.call_args_list}
        self.assertEqual(set(calls), {"furina", "kokomi", "columbina"})
        first = remote.call_args_list[0]
        kwargs = first.kwargs
        self.assertEqual(kwargs["api_key"], "sk-main-secret")
        self.assertEqual(
            kwargs["base_url"], server.SCENE_LLM_BASE_URL,
        )
        self.assertEqual(kwargs["model"], server.SCENE_LLM_MODEL)
        self.assertEqual(kwargs["fallback_key"], "sk-fallback-secret")
        self.assertEqual(kwargs["appraisal"].to_dict(), NEGATIVE)
        self.assertTrue(kwargs["character_desc"])
        self.assertIsNone(kwargs["contagion_bias"])

        bias_calls = [
            call for call in remote.call_args_list
            if call.kwargs["contagion_bias"]
        ]
        self.assertEqual(len(bias_calls), 2)

    def test_secret_keys_are_redacted_from_state_and_log(self):
        with mock.patch.object(
            server, "respond_llm", side_effect=fake_llm_result,
        ):
            with server._scene_lock:
                server._scene_reset(llm_config())
                state = server._scene_payload()
                turn = server._scene_turn({
                    "speaker_id": "user",
                    "text": "大家好。",
                    "appraisal": NEGATIVE,
                })

        for payload in (state, turn):
            text = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("sk-main-secret", text)
            self.assertNotIn("sk-fallback-secret", text)
            llm = payload["config"]["llm"]
            self.assertNotIn("api_key", llm)
            self.assertNotIn("fallback_key", llm)
            self.assertTrue(llm["has_api_key"])
            self.assertTrue(llm["has_fallback_key"])

    def test_blank_key_preserves_current_and_clear_flag_removes_it(self):
        with server._scene_lock:
            server._scene_reset(llm_config())
            server._scene_reset({
                **server._scene_default_config(),
                "llm": {"enabled": True, "api_key": ""},
            })
            kept = dict(server._scene_state["llm"])

            server._scene_reset({
                **server._scene_default_config(),
                "llm": {
                    "enabled": True,
                    "api_key": "",
                    "clear_api_key": True,
                },
            })
            cleared = dict(server._scene_state["llm"])

        self.assertEqual(kept["api_key"], "sk-main-secret")
        self.assertEqual(kept["fallback_key"], "sk-fallback-secret")
        self.assertEqual(cleared["api_key"], "")
        self.assertEqual(cleared["fallback_key"], "sk-fallback-secret")

    def test_llm_failure_keeps_local_template(self):
        with mock.patch.object(
            server, "respond_llm", side_effect=RuntimeError("offline"),
        ):
            with server._scene_lock:
                server._scene_reset(llm_config())
                result = server._scene_turn({
                    "speaker_id": "A",
                    "text": "你在吗？",
                    "appraisal": NEGATIVE,
                })

        self.assertEqual(result["turn"], 1)
        for actor in result["actors"].values():
            self.assertFalse(actor["llm_used"])
            self.assertEqual(actor["llm_error"], "offline")
            self.assertTrue(actor["utterance"])

    def test_llm_client_failure_flag_keeps_local_template(self):
        def fallback_result(engine, text, **kwargs):
            return {
                "utterance": "本地台词",
                "_llm_used": False,
                "_llm_backend": "",
                "_dominant": "neutral",
                "_state": {},
                "action": "approach",
                "tolerance": {"zone": "regulated"},
            }

        with mock.patch.object(
            server, "respond_llm", side_effect=fallback_result,
        ):
            with server._scene_lock:
                server._scene_reset(llm_config())
                result = server._scene_turn({
                    "speaker_id": "A",
                    "text": "你好。",
                    "appraisal": NEGATIVE,
                })

        for actor in result["actors"].values():
            self.assertFalse(actor["llm_used"])
            self.assertEqual(actor["llm_error"], "")
            self.assertEqual(actor["utterance"], "本地台词")

    def test_custom_character_key_is_optional(self):
        custom = {
            "custom_one": {
                "name": "小一",
                "desc": "自定义角色的描述。",
                "api_key": "sk-character-secret",
                "O": 0.5, "C": 0.5, "E": 0.5, "A": 0.5, "N": 0.5,
            },
        }
        config = server._scene_default_config()
        config["actors"] = [{"id": "A", "character": "custom_one"}]
        config["llm"] = dict(
            config["llm"],
            enabled=True,
            api_key="",
            use_character_key=True,
        )
        no_character_key = dict(
            config, llm=dict(config["llm"], use_character_key=False),
        )

        with mock.patch.object(
            server, "load_custom_chars", return_value=custom,
        ), mock.patch.object(
            server, "respond_llm", side_effect=fake_llm_result,
        ) as remote:
            with server._scene_lock:
                server._scene_reset(config)
                server._scene_turn({
                    "speaker_id": "user",
                    "text": "你好。",
                    "appraisal": NEGATIVE,
                })
                enabled_key = remote.call_args.kwargs["api_key"]
                self.assertEqual(
                    remote.call_args.kwargs["character_desc"],
                    "自定义角色的描述。",
                )

                server._scene_reset(no_character_key)
                server._scene_turn({
                    "speaker_id": "user",
                    "text": "你好。",
                    "appraisal": NEGATIVE,
                })
                disabled_key = remote.call_args.kwargs["api_key"]

        self.assertEqual(enabled_key, "sk-character-secret")
        self.assertIsNone(disabled_key)

    def test_llm_change_does_not_rebuild_scene_or_clear_log(self):
        with mock.patch.object(
            server, "respond_llm", side_effect=fake_llm_result,
        ):
            with server._scene_lock:
                base = server._scene_reset(server._scene_default_config())
                scene = server._scene_state["scene"]
                server._scene_turn({
                    "speaker_id": "A",
                    "text": "第一回合。",
                    "appraisal": NEGATIVE,
                })
                enabled = llm_config()
                enabled.update({
                    "world_id": base["world_id"],
                    "actors": base["config"]["actors"],
                })
                result = server._scene_turn({
                    "config": enabled,
                    "speaker_id": "A",
                    "text": "第二回合。",
                    "appraisal": NEGATIVE,
                })
                same_scene = server._scene_state["scene"] is scene
                log = server._scene_payload()["turn_log"]

        self.assertTrue(same_scene)
        self.assertEqual(result["turn"], 2)
        self.assertEqual(len(log), 2)
        self.assertTrue(result["config"]["llm"]["enabled"])

    def test_proactive_send_uses_llm(self):
        assessment = ProactiveAssessment(
            actor_id="A",
            target_id="user",
            urgency=0.9,
            eligible=True,
            blocked_reason="",
            reasons=("longing",),
            components={"longing": 0.9},
            elapsed_seconds=3600.0,
            pending=0,
            now=1000.0,
        )
        with mock.patch.object(
            server, "respond_llm", side_effect=fake_llm_result,
        ) as remote:
            with server._scene_lock:
                server._scene_reset(llm_config())
                scene = server._scene_state["scene"]
                scene.proactive.choose = lambda now=None: assessment
                result = server._scene_proactive({
                    "action": "send",
                    "now": 1000.0,
                })

        self.assertTrue(result["sent"])
        self.assertEqual(result["proactive_send"]["actor_id"], "A")
        self.assertEqual(remote.call_count, 3)
        for actor in result["actors"].values():
            self.assertTrue(actor["llm_used"])

    def test_scene_page_contains_llm_controls(self):
        html = Path(server.SCENE_HTML_PATH).read_text(encoding="utf-8")
        for element_id in (
            "llm-enabled",
            "llm-api-key",
            "llm-base-url",
            "llm-model",
            "llm-fallback-key",
            "llm-fallback-url",
            "llm-fallback-model",
            "llm-use-character-key",
            "llm-clear-key",
            "llm-clear-fallback-key",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("readLLMConfig", html)
        self.assertIn("fillLLMConfig", html)


if __name__ == "__main__":
    unittest.main()
