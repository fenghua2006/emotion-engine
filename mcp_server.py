"""Emotion Engine MCP server v0 —— 零依赖 stdio JSON-RPC。

暴露三个工具，供 Claude Code / Codex 等 MCP 客户端直接调用：
  tick   推进一轮情绪（可选 JEV 文本评估 / Noul 边界检测 / LLM 表达）
  recall 召回关系域记忆（按 character_id × user_id 隔离）
  state  读取当前情绪、耐受窗与引擎统计

传输：MCP stdio（每行一条 JSON-RPC 2.0 消息）。stdout 只写协议消息，
日志/异常写 stderr。没有 API key 时 tick 仍然可用（纯本地模式）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from typing import Any, Dict, List, Optional

from core import APPRAISAL_LIMITS
from engine import (
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SensitizationStore,
    respond,
    respond_llm,
)
from jev import JEV, make_appraisal
from memory import DEFAULT_DB_PATH, MemoryService, RelationshipMemoryStore
from noul import detect as detect_boundary

SERVER_NAME = "emotion-engine"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

_APPRAISAL_SCHEMA = {
    "type": "object",
    "properties": {
        name: {"type": "number", "minimum": lo, "maximum": hi}
        for name, (lo, hi) in APPRAISAL_LIMITS.items()
    },
    "additionalProperties": False,
}

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "tick",
        "description": (
            "推进一轮情绪。传入 text 时默认用 JEV 生成六维评估，"
            "并用 Noul 检测边界侵犯；也可直接传 appraisal。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "appraisal": _APPRAISAL_SCHEMA,
                "boundary_violation": {"type": "boolean"},
                "use_llm": {"type": "boolean", "default": False},
                "trace": {"type": "boolean", "default": True},
                "character_id": {"type": "string"},
                "user_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "recall",
        "description": "召回某个 (character_id, user_id) 关系域的相关记忆。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "character_id": {"type": "string"},
                "user_id": {"type": "string"},
                "budget_chars": {"type": "integer", "minimum": 100, "maximum": 4000},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "state",
        "description": "读取当前情绪状态、耐受窗与记忆统计。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "character_id": {"type": "string"},
                "user_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
]


def _default_engine() -> EmotionEngine:
    return EmotionEngine(
        state=EmotionalState(),
        personality=Personality(),
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


class EmotionMCPServer:
    """MCP 工具层；可注入 engine / memory / jev 便于测试。"""

    def __init__(self, *, engine: Optional[EmotionEngine] = None,
                 memory: Optional[MemoryService] = None,
                 jev: Optional[JEV] = None,
                 memory_db_path: str = DEFAULT_DB_PATH,
                 character_id: str = "default",
                 user_id: str = "default") -> None:
        self.engine = engine or _default_engine()
        self.jev = jev or JEV()
        self.memory_db_path = memory_db_path
        self.character_id = character_id
        self.user_id = user_id
        self._memory = memory
        self._memory_error: Optional[str] = None
        self._lock = threading.RLock()

    # ── tools ──────────────────────────────────────────

    def tick(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        text = str(arguments.get("text") or "").strip()
        appraisal_data = arguments.get("appraisal")
        if appraisal_data is not None and not isinstance(appraisal_data, dict):
            raise ValueError("appraisal must be an object")
        appraisal = make_appraisal(appraisal_data) if appraisal_data is not None else None
        if appraisal is None and not text:
            raise ValueError("text or appraisal is required")

        character_id = str(arguments.get("character_id") or self.character_id)
        user_id = str(arguments.get("user_id") or self.user_id)
        trace = bool(arguments.get("trace", True))
        use_llm = bool(arguments.get("use_llm", False))

        jev_result = None
        if appraisal is None:
            jev_result = self.jev.analyze(text)
            appraisal = jev_result.appraisal

        boundary_value = arguments.get("boundary_violation")
        boundary_signal = None
        if boundary_value is None:
            boundary_signal = detect_boundary(
                text, trust=self.engine.state.trust,
            ) if text else None
            boundary = bool(boundary_signal and boundary_signal.violated)
        else:
            boundary = bool(boundary_value)

        if use_llm:
            local = respond_llm(
                self.engine, text,
                appraisal=appraisal,
                character_id=character_id,
                relationship_memory=self._get_memory(),
                user_id=user_id,
                boundary_violation=boundary,
                trace=trace,
            )
        else:
            local = respond(
                self.engine, text,
                appraisal=appraisal,
                character_id=character_id,
                boundary_violation=boundary,
                trace=trace,
            )

        payload = {
            "character_id": character_id,
            "user_id": user_id,
            "state": self.engine.state.to_dict(),
            "atmosphere": round(self.engine.atmosphere, 6),
            "tolerance": self.engine.tolerance.to_dict(),
            "expression": local.get("expression"),
            "action": local.get("action"),
            "utterance": local.get("utterance", ""),
            "needs_llm": local.get("needs_llm", False),
            "shock_channels": local.get("shock_channels", []),
            "blends": local.get("blends", []),
            "jev": local.get("_jev", jev_result.to_dict() if jev_result else None),
            "boundary": local.get(
                "_boundary",
                boundary_signal.to_dict() if boundary_signal else None,
            ),
            "llm_used": bool(local.get("_llm_used", False)),
            "llm_backend": local.get("_llm_backend", ""),
            "trace": local.get("trace"),
        }
        return payload

    def recall(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        character_id = str(arguments.get("character_id") or self.character_id)
        user_id = str(arguments.get("user_id") or self.user_id)
        budget = int(arguments.get("budget_chars", 1200))
        budget = max(100, min(4000, budget))

        memory = self._get_memory()
        if memory is None:
            return {
                "query": query,
                "character_id": character_id,
                "user_id": user_id,
                "context": "",
                "error": self._memory_error or "memory unavailable",
            }

        result = memory.store.recall(query, character_id, user_id)
        context = memory.store.render_context(result, budget_chars=budget)
        return {
            "query": query,
            "character_id": character_id,
            "user_id": user_id,
            "context": context,
            "relationship": (
                {
                    "trust": result.relationship.trust,
                    "love": result.relationship.love,
                    "familiarity": result.relationship.familiarity,
                    "summary": result.relationship.summary,
                    "boundaries": list(result.relationship.boundaries),
                }
                if result.relationship else None
            ),
            "cards": [
                {
                    "summary": card.summary,
                    "kind": card.kind,
                    "salience": card.salience,
                    "confidence": card.confidence,
                    "created_at": card.created_at,
                }
                for card in result.cards
            ],
            "facts": [
                {
                    "key": fact.key,
                    "value": fact.value,
                    "category": fact.category,
                    "confidence": fact.confidence,
                }
                for fact in result.facts
            ],
            "open_loops": [
                {
                    "summary": loop.summary,
                    "importance": loop.importance,
                    "status": loop.status,
                    "due_at": loop.due_at,
                }
                for loop in result.open_loops
            ],
        }

    def state(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        character_id = str(arguments.get("character_id") or self.character_id)
        user_id = str(arguments.get("user_id") or self.user_id)
        return {
            "character_id": character_id,
            "user_id": user_id,
            "state": self.engine.state.to_dict(),
            "atmosphere": round(self.engine.atmosphere, 6),
            "tolerance": self.engine.tolerance.to_dict(),
            "stats": self.engine.memory.stats(),
        }

    # ── JSON-RPC ───────────────────────────────────────

    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(message, dict):
            return _error(None, -32600, "invalid request")
        message_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(message_id, -32600, "method must be a string")

        # 通知没有 id，不需要响应。
        if "id" not in message:
            return None

        if method == "initialize":
            params = message.get("params") or {}
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            return _result(message_id, {
                "protocolVersion": requested or DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        if method == "ping":
            return _result(message_id, {})
        if method == "tools/list":
            return _result(message_id, {"tools": TOOL_DEFINITIONS})
        if method == "tools/call":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                return _error(message_id, -32602, "params must be an object")
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _error(message_id, -32602, "arguments must be an object")
            try:
                payload = self.call_tool(str(name), arguments)
            except Exception as exc:
                return _result(message_id, {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                })
            return _result(message_id, {
                "content": [{
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False),
                }],
                "isError": False,
            })
        return _error(message_id, -32601, f"unknown method: {method}")

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if name == "tick":
                return self.tick(arguments)
            if name == "recall":
                return self.recall(arguments)
            if name == "state":
                return self.state(arguments)
        raise ValueError(f"unknown tool: {name}")

    # ── memory ─────────────────────────────────────────

    def close(self) -> None:
        """关闭延迟创建的关系记忆库；可重复调用。"""
        memory = self._memory
        self._memory = None
        if memory is None:
            return
        try:
            memory.store.close()
        except Exception:
            pass

    def _get_memory(self) -> Optional[MemoryService]:
        if self._memory is not None:
            return self._memory
        if self._memory_error:
            return None
        try:
            self._memory = MemoryService(
                RelationshipMemoryStore(self.memory_db_path),
            )
        except Exception as exc:
            self._memory_error = str(exc)
        return self._memory


def _result(message_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": message},
    }


def serve_stdio(server: EmotionMCPServer) -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response: Optional[Dict[str, Any]] = _error(None, -32700, str(exc))
        else:
            try:
                response = server.handle(message)
            except Exception as exc:  # 协议层兜底，不让单条消息杀死服务
                response = _error(
                    message.get("id") if isinstance(message, dict) else None,
                    -32603,
                    str(exc),
                )
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Emotion Engine MCP stdio server")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="关系记忆 SQLite 路径")
    parser.add_argument("--character-id", default="default")
    parser.add_argument("--user-id", default="default")
    args = parser.parse_args(argv)

    server = EmotionMCPServer(
        memory_db_path=args.db,
        character_id=args.character_id,
        user_id=args.user_id,
    )
    return serve_stdio(server)


if __name__ == "__main__":
    raise SystemExit(main())
