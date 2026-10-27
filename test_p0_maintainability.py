"""P0 可维护性护栏：表达层边界、tick 顺序、快照/记忆 schema 版本。"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import expression
import llm_client
from engine import (
    EmotionEngine,
    EmotionalState,
    MemoryStore,
    Personality,
    SNAPSHOT_VERSION,
    SensitizationStore,
    _migrate_snapshot_payload,
    init_db,
    load_snapshot,
    save_snapshot,
)
from memory import (
    MEMORY_SCHEMA_VERSION,
    RelationshipMemoryStore,
)

NOW = 1_700_000_000.0


def make_engine() -> EmotionEngine:
    personality = Personality()
    state = EmotionalState()
    for channel, value in personality.baseline().items():
        setattr(state, channel.value, value)
    state._last_update = NOW
    return EmotionEngine(
        state=state,
        personality=personality,
        memory=MemoryStore(),
        scars=SensitizationStore(),
    )


class ExpressionBoundaryTest(unittest.TestCase):
    def test_engine_reexports_expression_helpers(self):
        import engine as engine_module

        self.assertIs(engine_module._build_llm_prompt, expression._build_llm_prompt)
        self.assertIs(engine_module._dominant_channel, expression._dominant_channel)
        self.assertIs(engine_module._expression_params, expression._expression_params)
        self.assertIs(engine_module._mood_hint, expression._mood_hint)

    def test_expression_does_not_import_engine(self):
        source = pathlib.Path(expression.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from engine import", source)
        self.assertNotIn("import engine", source)

    def test_llm_client_without_key_is_safe(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm_client.call_llm("hello"), (None, None))

    def test_llm_client_success_is_tagged(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": " 你好 "}}],
                }).encode("utf-8")

        with patch("llm_client.urllib.request.urlopen", return_value=Response()):
            text, backend = llm_client.call_llm("hello", api_key="key")
        self.assertEqual(text, "你好")
        self.assertEqual(backend, "DeepSeek")

    def test_llm_client_falls_back(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": "fallback"}}],
                }).encode("utf-8")

        with patch(
            "llm_client.urllib.request.urlopen",
            side_effect=[RuntimeError("primary down"), Response()],
        ):
            text, backend = llm_client.call_llm(
                "hello", api_key="a", fallback_key="b",
            )
        self.assertEqual(text, "fallback")
        self.assertEqual(backend, "Kimi")


class TickStepOrderTest(unittest.TestCase):
    def test_tick_methods_exist(self):
        engine = make_engine()
        for name in (
            "_begin_tick", "_step_decay", "_step_slow_channels",
            "_step_appraisal", "_step_after_appraisal",
            "_step_interactions", "_step_shock",
            "_step_memory_and_saga", "_step_meta_and_tolerance",
            "_build_tick_result",
        ):
            self.assertTrue(callable(getattr(engine, name)), name)

    def test_tick_runs_steps_in_documented_order(self):
        engine = make_engine()
        order = []
        names = (
            "_begin_tick", "_step_decay", "_step_slow_channels",
            "_step_appraisal", "_step_after_appraisal",
            "_step_interactions", "_step_shock",
            "_step_memory_and_saga", "_step_meta_and_tolerance",
            "_build_tick_result",
        )
        for name in names:
            original = getattr(engine, name)

            def recording(*args, _name=name, _original=original, **kwargs):
                order.append(_name)
                return _original(*args, **kwargs)

            setattr(engine, name, recording)

        engine.tick(now=NOW + 60, trace=True)
        self.assertEqual(order, list(names))

    def test_trace_stages_still_present(self):
        engine = make_engine()
        trace = engine.tick(now=NOW + 60, trace=True)["trace"]
        for stage in (
            "before", "after_decay", "after_slow_channels",
            "after_appraisal", "after_atmosphere",
            "after_interaction", "final",
        ):
            self.assertTrue(trace[stage], stage)


class SnapshotSchemaTest(unittest.TestCase):
    def test_saved_payload_has_current_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "emotion.db")
            engine = make_engine()
            self.assertTrue(save_snapshot(engine, path, now=NOW))
            db = sqlite3.connect(path)
            try:
                raw = db.execute(
                    "SELECT state_json FROM snapshots ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
            finally:
                db.close()
            payload = json.loads(raw)
            self.assertEqual(payload["schema_version"], SNAPSHOT_VERSION)

    def test_versionless_payload_is_migrated(self):
        payload = _migrate_snapshot_payload({
            "state": {"joy": 0.4},
            "atmosphere": 0.1,
        })
        self.assertEqual(payload["schema_version"], SNAPSHOT_VERSION)
        self.assertEqual(payload["state"]["joy"], 0.4)

    def test_direct_channel_dict_is_v1(self):
        payload = _migrate_snapshot_payload({"joy": 0.4, "sadness": 0.2})
        self.assertEqual(payload["schema_version"], SNAPSHOT_VERSION)
        self.assertEqual(payload["state"]["joy"], 0.4)

    def test_future_or_invalid_version_is_rejected(self):
        self.assertIsNone(
            _migrate_snapshot_payload({
                "schema_version": SNAPSHOT_VERSION + 1,
                "state": {"joy": 0.4},
            })
        )
        self.assertIsNone(_migrate_snapshot_payload({"schema_version": "bad"}))
        self.assertIsNone(_migrate_snapshot_payload(["not", "a", "dict"]))

    def test_load_legacy_direct_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "emotion.db")
            engine = make_engine()
            init_db(path).close()
            db = sqlite3.connect(path)
            try:
                db.execute(
                    "INSERT INTO snapshots (state_json, atmosphere, saved_at) "
                    "VALUES (?,?,?)",
                    (json.dumps({"joy": 0.77}), 0.2, NOW),
                )
                db.commit()
            finally:
                db.close()

            self.assertTrue(load_snapshot(engine, path))
            self.assertAlmostEqual(engine.state.joy, 0.77)

    def test_load_future_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "emotion.db")
            engine = make_engine()
            before = engine.state.joy
            init_db(path).close()
            db = sqlite3.connect(path)
            try:
                db.execute(
                    "INSERT INTO snapshots (state_json, atmosphere, saved_at) "
                    "VALUES (?,?,?)",
                    (json.dumps({
                        "schema_version": SNAPSHOT_VERSION + 1,
                        "state": {"joy": 0.99},
                    }), 0.0, NOW),
                )
                db.commit()
            finally:
                db.close()

            self.assertFalse(load_snapshot(engine, path))
            self.assertAlmostEqual(engine.state.joy, before)


class MemorySchemaTest(unittest.TestCase):
    def test_new_database_gets_current_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")
            store = RelationshipMemoryStore(path)
            try:
                version = store.db.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, MEMORY_SCHEMA_VERSION)
                self.assertEqual(store.schema_version, MEMORY_SCHEMA_VERSION)
            finally:
                store.close()

    def test_old_database_is_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA user_version = 0")
            conn.commit()
            conn.close()

            store = RelationshipMemoryStore(path)
            try:
                version = store.db.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, MEMORY_SCHEMA_VERSION)
                tables = {
                    row[0] for row in store.db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("memory_cards", tables)
                self.assertIn("relationships", tables)
            finally:
                store.close()

    def test_future_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA user_version = 999")
            conn.commit()
            conn.close()

            with self.assertRaises(RuntimeError):
                RelationshipMemoryStore(path)


if __name__ == "__main__":
    unittest.main()
