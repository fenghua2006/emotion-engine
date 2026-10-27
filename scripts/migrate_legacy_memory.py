#!/usr/bin/env python3
"""把旧 engine.MemoryStoreDB 的 memories 表迁入关系域记忆。

用法：
    python scripts/migrate_legacy_memory.py \
        --legacy-db emotion.db \
        --memory-db memory.db \
        --character-id furina \
        --user-id alice

可重复执行；raw_ref 会去重。默认跳过自动生成的 "gc=..." 调试记忆。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from memory import (  # noqa: E402
    MemoryService,
    RelationshipMemoryStore,
    migrate_legacy_memories,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate legacy emotion.db memories to memory.db",
    )
    parser.add_argument("--legacy-db", default="emotion.db")
    parser.add_argument("--memory-db", default="memory.db")
    parser.add_argument("--character-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument(
        "--include-automatic", action="store_true",
        help='also migrate automatic "gc=..." rows',
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="write to an in-memory store and only report the result",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    legacy_path = os.path.abspath(args.legacy_db)
    if not os.path.exists(legacy_path):
        print(f"legacy db not found: {legacy_path}", file=sys.stderr)
        return 2

    if args.dry_run:
        store = RelationshipMemoryStore(":memory:")
    else:
        store = RelationshipMemoryStore(args.memory_db)

    try:
        stats = migrate_legacy_memories(
            legacy_path,
            store,
            character_id=args.character_id,
            user_id=args.user_id,
            skip_automatic=not args.include_automatic,
        )
        summary = ""
        if stats["migrated"] and not args.dry_run:
            service = MemoryService(store, auto_summary=True)
            summary = service.refresh_summary(
                args.character_id, args.user_id,
            )
        payload = {
            "legacy_db": legacy_path,
            "memory_db": ":memory:" if args.dry_run else os.path.abspath(
                args.memory_db,
            ),
            "character_id": args.character_id,
            "user_id": args.user_id,
            "dry_run": bool(args.dry_run),
            "stats": stats,
            "relationship_summary": summary,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
