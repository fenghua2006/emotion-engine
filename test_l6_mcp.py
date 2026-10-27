"""L6 MCP：stdio JSON-RPC、tick / recall / state 与错误处理。"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine import (
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SensitizationStore,
)
from mcp_server import EmotionMCPServer

ROOT = pathlib.Path(__file__).resolve().parent
NOW = 1_700_000_000.0


def make_engine(trust: float = 0.25) -> EmotionEngine:
    personality = Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    state.trust = trust
    state._last_update = NOW
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


def make_server(directory: str, trust: float = 0.25) -> EmotionMCPServer:
    return EmotionMCPServer(
        engine=make_engine(trust),
        memory_db_path=str(pathlib.Path(directory) / "memory.db"),
        character_id="test-char",
        user_id="test-user",
    )


def parse_tool_result(response):
    payload = response["result"]
    if payload.get("isError"):
        return payload
    return json.loads(payload["content"][0]["text"])


class ProtocolTest(unittest.TestCase):
    def test_initialize_echoes_protocol_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            })
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(response["result"]["serverInfo"]["name"], "emotion-engine")
        self.assertIn("tools", response["result"]["capabilities"])

    def test_notifications_do_not_get_a_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            self.assertIsNone(server.handle({
                "jsonrpc": "2.0", "method": "notifications/initialized",
            }))

    def test_ping(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(response["result"], {})

    def test_tools_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 3, "method": "tools/list",
            })
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(names, ["tick", "recall", "state"])

    def test_unknown_method_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 4, "method": "nope",
            })
        self.assertEqual(response["error"]["code"], -32601)

    def test_invalid_params_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": "bad",
            })
        self.assertEqual(response["error"]["code"], -32602)


class ToolTest(unittest.TestCase):
    def test_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "state", "arguments": {}},
            })
            payload = parse_tool_result(response)
        self.assertEqual(payload["character_id"], "test-char")
        self.assertIn("trust", payload["state"])
        self.assertIn("stats", payload)

    def test_tick_with_text_uses_jev_and_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp, trust=0.2)
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": "tick",
                    "arguments": {"text": "老婆，我回来了", "trace": True},
                },
            })
            payload = parse_tool_result(response)
        self.assertEqual(payload["jev"]["source"], "rules")
        self.assertTrue(payload["boundary"]["violated"])
        self.assertIn("before", payload["trace"])
        self.assertIn("trust", payload["state"])

    def test_tick_with_appraisal_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": "tick",
                    "arguments": {
                        "appraisal": {
                            "goal_relevance": 0.9,
                            "goal_conduciveness": -0.8,
                            "expectedness": 0.2,
                            "coping_potential": 0.2,
                            "other_agency": 0.9,
                            "social_evaluation": -0.5,
                        },
                    },
                },
            })
            payload = parse_tool_result(response)
        self.assertIsNone(payload["jev"])
        self.assertGreater(payload["state"]["anger"], 0.0)

    def test_tick_requires_text_or_appraisal(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "tick", "arguments": {}},
            })
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("required", result["content"][0]["text"])

    def test_unknown_tool_is_tool_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            })
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("unknown tool", result["content"][0]["text"])

    def test_recall_without_memories_returns_empty_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": "recall",
                    "arguments": {"query": "最近怎么样"},
                },
            })
            payload = parse_tool_result(response)
            server.close()
        self.assertEqual(payload["query"], "最近怎么样")
        self.assertIn("暂时没有相关记忆", payload["context"])
        self.assertNotIn("error", payload)

    def test_recall_requires_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "recall", "arguments": {}},
            })
        self.assertTrue(response["result"]["isError"])

    def test_tick_with_llm_uses_optional_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            with patch("engine.call_llm", return_value=(None, None)) as mocked:
                response = server.handle({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {
                        "name": "tick",
                        "arguments": {"text": "我好喜欢你", "use_llm": True},
                    },
                })
            payload = parse_tool_result(response)
            server.close()
        self.assertEqual(mocked.call_count, 1)
        self.assertFalse(payload["llm_used"])

    def test_tick_advances_state_across_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(tmp)
            first = server.tick({
                "text": "没想到你居然离开了",
                "appraisal": None,
            })
            second = server.tick({"text": "谢谢你一直陪我"})
        self.assertNotEqual(first["state"], second["state"])


class StdioTest(unittest.TestCase):
    def test_stdio_json_rpc_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            requests = "\n".join([
                json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                }),
                json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list",
                }),
                "",
            ])
            completed = subprocess.run(
                [sys.executable, "mcp_server.py", "--db",
                 str(pathlib.Path(tmp) / "memory.db")],
                input=requests,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=30,
                cwd=str(ROOT),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        first, second = (json.loads(line) for line in lines)
        self.assertEqual(first["result"]["serverInfo"]["name"], "emotion-engine")
        self.assertEqual(
            [tool["name"] for tool in second["result"]["tools"]],
            ["tick", "recall", "state"],
        )


if __name__ == "__main__":
    unittest.main()
