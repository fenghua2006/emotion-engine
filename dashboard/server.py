"""
仪表盘 HTTP 服务 — 实时可视化 EmotionEngine 状态
================================================
通用插件，不绑定角色——任何蒸馏角色均可挂载。

用法:
  uv run python dashboard/server.py                       # 默认角色
  uv run python dashboard/server.py --char furina          # 芙宁娜
  uv run python dashboard/server.py --char kokomi          # 心海
  uv run python dashboard/server.py --char columbina       # 哥伦比娅
  uv run python dashboard/server.py --port 8080 --char furina

浏览器打开 http://localhost:9020
"""

import sys
import os
import json
import copy
import time
import threading
import argparse
import importlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import (
    EmotionEngine, EmotionalState, Personality, Appraisal, Channel,
    APPRAISAL_LIMITS, MemoryStore, SensitizationStore, _dominant_channel,
    respond, respond_llm
)
from memory import MemoryService, RelationshipMemoryStore
from jev import analyze as analyze_text, make_appraisal
from noul import detect as detect_boundary
from contagion import (
    ContagionEdge,
    ContagionNetwork,
    ContagionPair,
    DirectionalRelationship,
)
from social import FriendScene, PeerRelationshipStore, SceneActor
from proactive import ProactiveConfig

# ═══════════════════════════════════════════════
# 角色加载器
# ═══════════════════════════════════════════════

def load_character(char_id: str):
    """动态加载角色模块，返回 (engine, appraise_fn, display_name, char_id)"""
    try:
        mod = importlib.import_module(f"characters.{char_id}")
    except ModuleNotFoundError:
        print(f"[!] 角色 '{char_id}' 未找到，使用默认角色")
        return _default_character()

    # 角色模块约定: create_<id>() 返回 EmotionEngine 实例
    factory = getattr(mod, f"create_{char_id}", None)
    if not factory:
        print(f"[!] 角色 '{char_id}' 缺少 create_{char_id}()，使用默认角色")
        return _default_character()

    appraise = getattr(mod, f"{char_id}_appraise", None)
    name = getattr(mod, f"{char_id}_name", char_id)
    engine = factory()
    return engine, appraise, name, char_id


def _default_character():
    """内置通用角色——中庸 OCEAN，无特殊触发映射"""
    pers = Personality(
        openness=0.5, conscientiousness=0.5, extraversion=0.5,
        agreeableness=0.5, neuroticism=0.5
    )
    engine = EmotionEngine(
        state=EmotionalState(), personality=pers,
        memory=MemoryStore(), scars=SensitizationStore()
    )
    return engine, None, "Default", "default"


# ═══════════════════════════════════════════════
# 通用事件分类（无角色关键词时用）
# ═══════════════════════════════════════════════

def classify_generic(text: str) -> Appraisal:
    """根据自然语言推断 appraisal，不依赖角色关键词"""
    t = text.strip()
    # 正面
    if any(w in t for w in ["爱", "喜欢", "想你了", "想你", "谢谢", "感谢", "棒", "厉害", "哈哈哈"]):
        return Appraisal(goal_relevance=0.8, goal_conduciveness=0.6,
                         expectedness=0.3, other_agency=0.7,
                         coping_potential=0.4, social_evaluation=0.7)
    # 负面——攻击
    if any(w in t for w in ["骗", "假", "撒谎", "滚", "讨厌", "恶心", "烦"]):
        return Appraisal(goal_relevance=0.8, goal_conduciveness=-0.6,
                         expectedness=0.3, other_agency=0.8,
                         coping_potential=0.2, social_evaluation=-0.5)
    # 负面——离别
    if any(w in t for w in ["再见", "拜拜", "走了", "下了", "离开"]):
        return Appraisal(goal_relevance=0.6, goal_conduciveness=-0.3,
                         expectedness=0.6, other_agency=0.7,
                         coping_potential=0.4, social_evaluation=0.0)
    # 道歉
    if any(w in t for w in ["对不起", "抱歉", "怪我", "是我的错"]):
        return Appraisal(goal_relevance=0.7, goal_conduciveness=0.4,
                         expectedness=0.4, other_agency=0.9,
                         coping_potential=0.4, social_evaluation=0.5)
    # 孤独/悲伤
    if any(w in t for w in ["孤单", "累", "孤独", "疲惫", "厌倦", "难过", "伤心"]):
        return Appraisal(goal_relevance=0.8, goal_conduciveness=-0.3,
                         expectedness=0.6, other_agency=0.2,
                         coping_potential=0.3, social_evaluation=0.0)
    # 惊讶/疑问
    if any(w in t for w in ["为什么", "怎么", "??", "？？", "什么", "不会吧"]):
        return Appraisal(goal_relevance=0.5, goal_conduciveness=0.0,
                         expectedness=0.2, other_agency=0.3,
                         coping_potential=0.6, social_evaluation=0.0)
    # 默认闲聊
    return Appraisal(goal_relevance=0.4, goal_conduciveness=0.05,
                     expectedness=0.6, other_agency=0.3,
                     coping_potential=0.6, social_evaluation=0.1)


# ═══════════════════════════════════════════════
# 全局引擎实例（由 main 注入——放 _state dict 避免 global 声明）
# ═══════════════════════════════════════════════

_lock = threading.Lock()
MAX_HISTORY = 300
_no_save = False  # 由 main() 设置
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MEMORY_DB_PATH = os.path.join(DASHBOARD_DIR, "_relationship_memory.db")
DEFAULT_SOCIAL_DB_PATH = os.path.join(DASHBOARD_DIR, "_social_relationships.db")
DEFAULT_USER_ID = "dashboard"
SCENE_MAX_ACTORS = 4
SCENE_MAX_TEXT = 2000
SCENE_DEFAULT_ACTORS = ("furina", "kokomi", "columbina")
SAVE_PATH = os.path.join(DASHBOARD_DIR, "_emotion_save.json")
CHARS_PATH = os.path.join(DASHBOARD_DIR, "_custom_chars.json")
CHAT_LOG_PATH = os.path.join(DASHBOARD_DIR, "_chat_log.json")
SOVITS_URL = "http://127.0.0.1:9880/tts"  # GPT-SoVITS api_v2

# ═══════════════════════════════════════════════
# 本地存档
# ═══════════════════════════════════════════════

def save_to_disk():
    """将当前引擎状态序列化到 JSON"""
    if _no_save:
        return
    engine = _state["engine"]
    if not engine:
        return
    data = {
        "char_id": _state["char_id"],
        "char_name": _state["char_name"],
        "memory_id": _current_memory_id(),
        "user_id": _current_user_id(),
        "custom_desc": _state.get("custom_desc", ""),
        "custom_api_key": _state.get("custom_api_key", ""),
        "atmosphere": engine.atmosphere,
        "state": engine.state.to_dict(),
        "last_update": engine.state._last_update,
        "events_today": engine.events_today,
        "positive_events": engine.positive_events,
        "tolerance": engine.tolerance.to_dict(),
        "scars": {tag: {"count": p.trigger_count, "shift": p.threshold_shift,
                        "positive": p.positive_counter, "last": p.last_triggered}
                  for tag, p in engine.scars.patterns.items()},
        "saved_at": time.time(),
    }
    try:
        with open(SAVE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 存档失败不炸


def load_from_disk():
    """从 JSON 恢复引擎状态。返回恢复后的 engine 或 None"""
    if not os.path.exists(SAVE_PATH):
        return None
    try:
        with open(SAVE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    char_id = data.get("char_id", "default")
    engine, appraise_fn, char_name, _ = load_character(char_id)

    # 恢复情绪状态
    for ch in Channel:
        if ch.value in data.get("state", {}):
            setattr(engine.state, ch.value, data["state"][ch.value])

    engine.state._last_update = data.get("last_update", time.time())
    engine.atmosphere = data.get("atmosphere", -0.3)
    engine.events_today = data.get("events_today", 0)
    engine.positive_events = data.get("positive_events", 0)
    if "tolerance" in data:
        from tolerance import ToleranceState
        engine.tolerance = ToleranceState.from_dict(data["tolerance"])

    # 恢复伤疤
    for tag, s in data.get("scars", {}).items():
        from engine import SensitizationPattern
        p = SensitizationPattern(
            tag=tag, trigger_count=s.get("count", 1),
            threshold_shift=s.get("shift", 0.0),
            positive_counter=s.get("positive", 0),
            last_triggered=s.get("last", time.time())
        )
        engine.scars.patterns[tag] = p

    # 恢复角色描述
    _state["custom_desc"] = data.get("custom_desc", "")
    _state["custom_api_key"] = data.get("custom_api_key", "")

    # 结算离线时间（wake 会改写 _last_update，所以先算）
    offline_hours = max(0.0, (time.time() - engine.state._last_update) / 3600.0)
    engine.wake()

    print(f"[save] 存档已恢复 - {char_name}，离线 {round(offline_hours, 1)}h")
    return engine, appraise_fn, char_name, char_id


def delete_save():
    """删除存档"""
    try:
        os.remove(SAVE_PATH)
    except Exception:
        pass


def load_custom_chars() -> dict:
    """加载用户自定义角色"""
    if not os.path.exists(CHARS_PATH):
        return {}
    try:
        with open(CHARS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_custom_chars(chars: dict):
    """保存用户自定义角色"""
    try:
        with open(CHARS_PATH, "w", encoding="utf-8") as f:
            json.dump(chars, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def save_chat_message(char_name: str, user_text: str, char_text: str):
    """追加一条聊天记录"""
    try:
        logs = []
        if os.path.exists(CHAT_LOG_PATH):
            with open(CHAT_LOG_PATH, "r", encoding="utf-8") as f:
                logs = json.load(f)
        logs.append({
            "t": time.time(),
            "char": char_name,
            "user": user_text,
            "reply": char_text,
        })
        # 保留最近 2000 条
        if len(logs) > 2000:
            logs = logs[-2000:]
        with open(CHAT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_chat_log(count: int = 100) -> list:
    """读取最近 N 条聊天记录"""
    try:
        if not os.path.exists(CHAT_LOG_PATH):
            return []
        with open(CHAT_LOG_PATH, "r", encoding="utf-8") as f:
            logs = json.load(f)
        return logs[-count:]
    except Exception:
        return []


def get_all_characters() -> list:
    """返回所有可用角色列表（自动扫描 characters/ 目录 + 自定义存盘角色）"""
    chars_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "characters")
    builtin = [{"id": "default", "name": "Default", "builtin": True}]
    seen = {"default"}

    # 扫描 characters/*.py 自动发现角色
    if os.path.isdir(chars_dir):
        for fname in sorted(os.listdir(chars_dir)):
            if fname.startswith("_") or not fname.endswith(".py"):
                continue
            cid = fname[:-3]  # 去 .py
            if cid in seen:
                continue
            seen.add(cid)
            # 尝试读取角色名
            try:
                mod = importlib.import_module(f"characters.{cid}")
                name = getattr(mod, f"{cid}_name", cid)
            except Exception:
                name = cid
            builtin.append({"id": cid, "name": name, "builtin": True})

    # 用户保存在 _custom_chars.json 的角色
    custom_chars = load_custom_chars()
    custom = [
        {"id": cid, "name": cdata.get("name", cid), "builtin": False}
        for cid, cdata in custom_chars.items()
        if cid not in seen
    ]
    return builtin + custom

_state = {
    "engine": None,      # EmotionEngine
    "appraise_fn": None, # 角色专属 appraisal 函数
    "char_name": "",     # 显示名称
    "char_id": "",       # 角色 ID
    "memory_id": "",     # 持久记忆角色键（解决未保存 custom 共用 custom）
    "user_id": DEFAULT_USER_ID,
    "memory_service": None,  # MemoryService；所有持久记忆只走关系域存储
    "memory_db_path": DEFAULT_MEMORY_DB_PATH,
    "memory_last_error": "",
    "history": [],       # 历史数据点
    "custom_desc": "",   # 角色描述（LLM prompt 用）
}


def _memory_character_id(char_id: str, char_name: str = "") -> str:
    """持久记忆的角色键。

    未保存的自定义角色 API 统一使用 char_id='custom'，如果直接拿它做
    记忆键，不同名字的自定义角色会互相污染。这里按显示名拆开；保存过
    的自定义角色本来就有唯一 id，直接使用。
    """
    cid = str(char_id or "default").strip() or "default"
    if cid == "custom":
        name = str(char_name or "").strip() or "unnamed"
        return f"custom:{name}"
    return cid


def _current_memory_id() -> str:
    return _state.get("memory_id") or _memory_character_id(
        _state.get("char_id", ""), _state.get("char_name", ""),
    )


def _current_user_id() -> str:
    return _state.get("user_id") or DEFAULT_USER_ID


def _get_memory_service() -> MemoryService | None:
    """懒加载关系域记忆服务。

    所有角色共享同一个 SQLite 文件，但每次读写都带 character_id +
    user_id；这是关系域存储的硬边界。建库失败时仪表盘仍可本地运行。
    """
    service = _state.get("memory_service")
    if service is not None:
        return service
    if _state.get("memory_last_error"):
        return None
    try:
        service = MemoryService(
            RelationshipMemoryStore(
                _state.get("memory_db_path") or DEFAULT_MEMORY_DB_PATH,
            ),
        )
    except Exception as exc:
        _state["memory_last_error"] = str(exc)
        return None
    _state["memory_service"] = service
    return service


def _close_memory_service() -> None:
    service = _state.get("memory_service")
    _state["memory_service"] = None
    if service is None:
        return
    try:
        service.store.close()
    except Exception:
        pass


def _memory_appraisal_prior():
    service = _get_memory_service()
    if service is None:
        return None
    try:
        return service.appraisal_prior(
            _current_memory_id(), _current_user_id(),
        )
    except Exception as exc:
        _state["memory_last_error"] = str(exc)
        return None


def _remember_turn(engine, user_text: str, assistant_text: str,
                   appraisal=None) -> list:
    """把本轮用户话语交给关系域记忆抽取；失败不打断对话。"""
    service = _get_memory_service()
    if service is None:
        return []
    try:
        return service.remember_turn(
            character_id=_current_memory_id(),
            user_id=_current_user_id(),
            user_text=user_text,
            assistant_text=assistant_text,
            appraisal=appraisal,
            emotion_snap=engine.state.to_dict(),
        )
    except Exception as exc:
        _state["memory_last_error"] = str(exc)
        return []

# 内置角色描述——确保 LLM 拿到正确人设
_BUILTIN_DESCS = {
    "furina": "你是芙宁娜·德·枫丹，前水神，以人类之躯扮演神明500年。外层浮夸自信戏剧化，内层疲惫孤独。回复自然口语化，一两句话。",
    "kokomi": "你是珊瑚宫心海，海祇岛现人神巫女，天才军师。内向但睿智，社交场合容易疲惫。说话温柔精准。",
    "columbina": "你是哥伦比娅，孤独的月神。沉默寡言，语句稀疏意味深长。情感淡漠却不冷酷。",
}


def tolerance_history_point(data: dict) -> dict:
    """把 /api/state 响应压成历史点，保留耐受窗曲线所需字段。"""
    tolerance = dict(data.get("tolerance") or {})
    return {
        "t": data["timestamp"],
        "felt": dict(data["felt"]),
        "atmosphere": data["atmosphere"],
        "tolerance": tolerance,
    }


def build_state_response() -> dict:
    engine = _state["engine"]
    s = engine.state
    dom = _dominant_channel(s)
    felt = s.felt_all()
    scars = engine.scars.all_scars()

    return {
        "timestamp": time.time(),
        "character": _state["char_name"],
        "character_id": _state["char_id"],
        "raw": s.to_dict(),
        "felt": felt,
        "atmosphere": round(-engine.atmosphere, 3),  # 翻转：正值=轻松，负值=紧绷
        "tolerance": engine.tolerance.to_dict(),
        "dominant": dom or "neutral",
        "dominant_value": round(getattr(s, dom.replace("_dominant", ""), 0), 3) if dom and dom.replace("_dominant", "") in s.to_dict() else 0,
        "shock_channels": [],
        "scars": [{"tag": s.tag, "count": s.trigger_count, "shift": round(s.threshold_shift, 3)}
                  for s in scars] if scars else [],
        "memory": {
            "flash": _count_memories(engine, "flash"),
            "long_term": _count_memories(engine, "long_term"),
            "short_term": _count_memories(engine, "short_term"),
            "relationship": _relationship_memory_stats(),
        },
        "memory_scope": {
            "character_id": _current_memory_id(),
            "user_id": _current_user_id(),
        },
        "needs_llm": s.surprise > 0.8 or len(scars) > 4,
    }


def _count_memories(engine, tier: str) -> int:
    """兼容内存 MemoryStore 与旧 MemoryStoreDB。"""
    try:
        stats = engine.memory.stats()
        return int(stats.get(f"{tier}_count", 0))
    except Exception:
        pass
    try:
        return engine.memory.db.execute(
            "SELECT COUNT(*) FROM memories WHERE tier=?", (tier,)
        ).fetchone()[0]
    except Exception:
        return 0


def _relationship_memory_stats() -> dict:
    """只读取已初始化服务；同时让 /api/state 保持无副作用。"""
    service = _state.get("memory_service")
    if service is None:
        return {"cards": 0, "facts": 0, "open_loops": 0, "relationships": 0}
    try:
        return service.store.stats(
            _current_memory_id(), _current_user_id(),
        )
    except Exception:
        return {"cards": 0, "facts": 0, "open_loops": 0, "relationships": 0}


# ═══════════════════════════════════════════════
# L7 多角色感染实验台
# ═══════════════════════════════════════════════

MAX_CONTAGION_NODES = 6
MAX_CONTAGION_EDGES = 20
MAX_CONTAGION_STEPS = 20
CONTAGION_HISTORY_LIMIT = 240
CONTAGION_CHANNELS = (
    "joy", "sadness", "anger", "fear", "love",
    "disgust", "surprise", "trust", "longing", "guilt",
)

_contagion_lock = threading.Lock()
_contagion_state = {
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
}


def _contagion_float(value, default=0.0, lo=None, hi=None):
    """把前端传来的任意数值安全地限制到闭区间。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number or number in (float("inf"), float("-inf")):
        number = float(default)
    if lo is not None:
        number = max(lo, number)
    if hi is not None:
        number = min(hi, number)
    return number


def _contagion_int(value, default, lo, hi):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = int(default)
    return max(lo, min(hi, number))


def _contagion_boundaries(value):
    if isinstance(value, str):
        raw = value.replace("，", ",").split(",")
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = []
    boundaries = []
    for item in raw:
        text = str(item).strip()
        if text and text not in boundaries:
            boundaries.append(text)
        if len(boundaries) >= 20:
            break
    return tuple(boundaries)


def _contagion_engine(character_id: str):
    """新建完全隔离的引擎；不读取单角色仪表盘的存档。"""
    custom = load_custom_chars().get(character_id)
    if custom:
        personality = Personality(
            openness=float(custom.get("O", 0.5)),
            conscientiousness=float(custom.get("C", 0.5)),
            extraversion=float(custom.get("E", 0.5)),
            agreeableness=float(custom.get("A", 0.5)),
            neuroticism=float(custom.get("N", 0.5)),
        )
        engine = EmotionEngine(
            state=EmotionalState(), personality=personality,
            memory=MemoryStore(), scars=SensitizationStore(),
        )
        engine.state._last_update = time.time()
        return engine, custom.get("name", character_id), character_id

    if character_id == "default":
        engine, _appraise_fn, name, resolved_id = _default_character()
    else:
        try:
            engine, _appraise_fn, name, resolved_id = load_character(character_id)
        except Exception:
            engine, _appraise_fn, name, resolved_id = _default_character()
    # 多角色实验台必须保证每个节点拥有独立的记忆与伤疤。
    engine.memory = MemoryStore()
    engine.scars = SensitizationStore()
    engine.state._last_update = time.time()
    return engine, name, resolved_id


def _contagion_node(raw, index: int):
    if not isinstance(raw, dict):
        raise ValueError("node must be an object")
    node_id = str(raw.get("id") or chr(65 + index)).strip()
    if not node_id:
        raise ValueError("node id must not be empty")
    character = str(raw.get("character") or "default").strip() or "default"
    trust = _contagion_float(raw.get("trust"), 0.2, 0.0, 1.0)
    love = _contagion_float(raw.get("love"), 0.2, 0.0, 2.0)
    familiarity = _contagion_float(raw.get("familiarity"), 0.0, 0.0, 1.0)
    boundaries = _contagion_boundaries(raw.get("boundaries"))
    return {
        "id": node_id,
        "character": character,
        "trust": trust,
        "love": love,
        "familiarity": familiarity,
        "boundaries": boundaries,
    }


def _contagion_relationship(node):
    return DirectionalRelationship(
        trust=node["trust"],
        love=node["love"],
        familiarity=node["familiarity"],
        boundaries=node["boundaries"],
    )


def _contagion_signature(config):
    return json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)


def _contagion_snapshot(engine):
    state = engine.state
    dominant = _dominant_channel(state) or "neutral"
    return {
        "raw": {key: round(float(value), 6) for key, value in state.to_dict().items()},
        "felt": {key: round(float(value), 6) for key, value in state.felt_all().items()},
        "dominant": dominant,
    }


def _contagion_payload(rebuilt=False):
    state = _contagion_state
    nodes = {}
    for node_id in state["node_order"]:
        engine = state["engines"][node_id]
        snap = _contagion_snapshot(engine)
        initial = state["initial"].get(node_id, snap)
        result = state["last_results"].get(node_id, {})
        delta = {
            key: round(snap["raw"].get(key, 0.0) - initial["raw"].get(key, 0.0), 6)
            for key in CONTAGION_CHANNELS
        }
        nodes[node_id] = {
            "id": node_id,
            "name": state["nodes"][node_id]["name"],
            "character": state["nodes"][node_id]["character"],
            "dominant": snap["dominant"],
            "raw": snap["raw"],
            "felt": snap["felt"],
            "initial": initial["raw"],
            "delta": delta,
            "contagion_bias": result.get("contagion_bias", {}),
            "history": list(state["history"].get(node_id, [])),
        }

    strengths = {}
    if state["pair"] is not None:
        pair = state["pair"]
        source_id = state["source_id"]
        target_id = state["target_id"]
        strengths[f"{source_id}->{target_id}"] = round(
            pair.source_to_target_strength, 6,
        )
        if state["pair_bidirectional"]:
            strengths[f"{target_id}->{source_id}"] = round(
                pair.target_to_source_strength, 6,
            )
    elif state["network"] is not None:
        for edge in state["network"].edges:
            strengths[f"{edge.source}->{edge.target}"] = round(edge.strength, 6)

    return {
        "initialized": True,
        "turn": state["turn"],
        "mode": state["mode"],
        "pair_bidirectional": state["pair_bidirectional"],
        "rebuilt": bool(rebuilt),
        "nodes": nodes,
        "strengths": strengths,
        "node_order": list(state["node_order"]),
        "edges": (
            [
                {"source": edge.source, "target": edge.target,
                 "strength": round(edge.strength, 6)}
                for edge in state["network"].edges
            ]
            if state["network"] is not None else []
        ),
    }


def _build_contagion_state(config):
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    mode = str(config.get("mode") or "network").strip().lower()
    if mode not in ("network", "pair"):
        raise ValueError("mode must be 'network' or 'pair'")

    raw_nodes = config.get("nodes")
    if not isinstance(raw_nodes, list) or len(raw_nodes) < 2:
        raise ValueError("至少需要两个节点")
    if len(raw_nodes) > MAX_CONTAGION_NODES:
        raise ValueError(f"最多 {MAX_CONTAGION_NODES} 个节点")

    nodes = {}
    engines = {}
    order = []
    for index, raw in enumerate(raw_nodes):
        node = _contagion_node(raw, index)
        node_id = node["id"]
        if node_id in nodes:
            raise ValueError(f"节点 id 重复: {node_id}")
        engine, name, resolved_id = _contagion_engine(node["character"])
        engine.state.trust = node["trust"]
        engine.state.love = node["love"]
        node["name"] = name
        node["resolved_character"] = resolved_id
        nodes[node_id] = node
        engines[node_id] = engine
        order.append(node_id)

    if mode == "pair" and len(order) != 2:
        raise ValueError("pair 模式需要恰好两个节点")

    source_id = str(config.get("source") or order[0]).strip()
    target_id = str(config.get("target") or order[1]).strip()
    if source_id not in engines or target_id not in engines:
        raise ValueError("pair 模式的 source / target 必须是已存在的节点 id")
    if source_id == target_id:
        raise ValueError("pair 模式的 source 与 target 不能相同")

    gain = _contagion_float(config.get("gain"), 0.2, 0.0, 1.0)
    cap = _contagion_float(config.get("cap"), 0.08, 0.0, 0.2)
    hop_decay = _contagion_float(config.get("hop_decay"), 0.5, 0.0, 1.0)
    max_hops = _contagion_int(config.get("max_hops"), 3, 1, 8)
    minutes_per_step = _contagion_float(
        config.get("minutes_per_step"), 10.0, 1.0, 120.0,
    )

    pair = None
    pair_bidirectional = False
    network = None
    if mode == "network":
        raw_edges = config.get("edges") or []
        if not isinstance(raw_edges, list):
            raise ValueError("edges must be a list")
        if len(raw_edges) > MAX_CONTAGION_EDGES:
            raise ValueError(f"最多 {MAX_CONTAGION_EDGES} 条边")
        edges = []
        for raw in raw_edges:
            if not isinstance(raw, dict):
                raise ValueError("edge must be an object")
            edges.append(ContagionEdge(
                str(raw.get("source", "")).strip(),
                str(raw.get("target", "")).strip(),
                _contagion_float(raw.get("strength"), 1.0, 0.0, 1.0),
            ))
        network = ContagionNetwork(
            engines, edges, gain=gain, cap=cap,
            hop_decay=hop_decay, max_hops=max_hops,
        )
    else:
        pair_bidirectional = str(
            config.get("pair_direction") or "one-way"
        ).strip().lower() == "bidirectional"
        strength_mode = str(
            config.get("strength_mode") or "explicit"
        ).strip().lower()
        if strength_mode == "auto":
            pair = ContagionPair.from_relationship(
                engines[source_id], engines[target_id],
                gain=gain, cap=cap,
            )
        elif strength_mode == "directional":
            pair = ContagionPair.from_directional_relationship(
                engines[source_id], engines[target_id],
                gain=gain, cap=cap,
                source_relationship=_contagion_relationship(nodes[source_id]),
                target_relationship=_contagion_relationship(nodes[target_id]),
            )
        else:
            pair = ContagionPair(
                engines[source_id], engines[target_id],
                gain=gain, cap=cap,
                strength=_contagion_float(config.get("strength"), 1.0, 0.0, 1.0),
            )

    base_now = time.time()
    for engine in engines.values():
        engine.state._last_update = base_now

    state = _contagion_state
    state["signature"] = _contagion_signature(config)
    state["config"] = copy.deepcopy(config)
    state["nodes"] = nodes
    state["engines"] = engines
    state["node_order"] = tuple(order)
    state["mode"] = mode
    state["pair"] = pair
    state["pair_bidirectional"] = pair_bidirectional
    state["network"] = network
    state["source_id"] = source_id if mode == "pair" else None
    state["target_id"] = target_id if mode == "pair" else None
    state["turn"] = 0
    state["last_now"] = base_now
    state["minutes_per_step"] = minutes_per_step
    state["initial"] = {
        node_id: _contagion_snapshot(engine)
        for node_id, engine in engines.items()
    }
    state["last_results"] = {}
    state["history"] = {node_id: [] for node_id in order}
    return state


def _contagion_appraisal_map(raw):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("appraisals must be an object keyed by node id")
    result = {}
    for node_id, value in raw.items():
        if node_id not in _contagion_state["engines"]:
            raise ValueError(f"unknown appraisal node: {node_id}")
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError(f"appraisal for {node_id} must be an object")
        result[node_id] = make_appraisal(value)
    return result


def _run_contagion_tick(config, appraisals, steps=1):
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    signature = _contagion_signature(config)
    rebuilt = _contagion_state["signature"] != signature
    if rebuilt or not _contagion_state["engines"]:
        _build_contagion_state(config)

    appraisal_map = _contagion_appraisal_map(appraisals)
    step_count = _contagion_int(steps, 1, 1, MAX_CONTAGION_STEPS)
    state = _contagion_state

    for _ in range(step_count):
        now = max(time.time(), state["last_now"]) + state["minutes_per_step"] * 60.0
        if state["mode"] == "network":
            results = state["network"].tick(appraisal_map, now=now)
        else:
            source_appraisal = appraisal_map.get(state["source_id"])
            target_appraisal = appraisal_map.get(state["target_id"])
            if state["pair_bidirectional"]:
                source_result, target_result = state["pair"].tick_bidirectional(
                    source_appraisal, target_appraisal, now=now,
                )
            else:
                source_result, target_result = state["pair"].tick_pair(
                    source_appraisal, target_appraisal, now=now,
                )
            results = {
                state["source_id"]: source_result,
                state["target_id"]: target_result,
            }

        state["turn"] += 1
        state["last_now"] = now
        state["last_results"] = results
        for node_id, result in results.items():
            snap = _contagion_snapshot(state["engines"][node_id])
            point = {
                "turn": state["turn"],
                "dominant": snap["dominant"],
                "contagion_bias": result.get("contagion_bias", {}),
            }
            point.update(snap["raw"])
            state["history"].setdefault(node_id, []).append(point)
            if len(state["history"][node_id]) > CONTAGION_HISTORY_LIMIT:
                state["history"][node_id].pop(0)

    return _contagion_payload(rebuilt=rebuilt)


def _contagion_reset(config):
    _build_contagion_state(config)
    return _contagion_payload(rebuilt=True)


# ═══════════════════════════════════════════════
# L8 社交场景（/scene）
# ═══════════════════════════════════════════════

_scene_lock = threading.Lock()
_scene_state = {
    "signature": None,
    "config": None,
    "scene": None,
    "store": None,
    "db_path": DEFAULT_SOCIAL_DB_PATH,
    "turn_log": [],
    "last_payload": None,
}
SCENE_HISTORY_LIMIT = 40
PROACTIVE_ACTION_CHECK = "check"
PROACTIVE_ACTION_SEND = "send"


def _scene_default_config():
    return {
        "world_id": "dashboard",
        "gain": 0.2,
        "cap": 0.08,
        "hop_decay": 0.5,
        "max_hops": 2,
        "actors": [
            {"id": chr(65 + index), "character": character}
            for index, character in enumerate(SCENE_DEFAULT_ACTORS)
        ],
        "proactive": ProactiveConfig().to_dict(),
    }


def _scene_config(raw):
    raw = raw if isinstance(raw, dict) else {}
    world_id = str(raw.get("world_id") or "dashboard").strip()[:64]
    if not world_id:
        world_id = "dashboard"
    gain = _contagion_float(raw.get("gain"), 0.2, 0.0, 1.0)
    cap = _contagion_float(raw.get("cap"), 0.08, 0.0, 0.2)
    hop_decay = _contagion_float(raw.get("hop_decay"), 0.5, 0.0, 1.0)
    max_hops = _contagion_int(raw.get("max_hops"), 2, 1, 8)

    raw_actors = raw.get("actors")
    if not isinstance(raw_actors, (list, tuple)) or not raw_actors:
        raw_actors = _scene_default_config()["actors"]

    actors = []
    seen = set()
    for index, item in enumerate(list(raw_actors)[:SCENE_MAX_ACTORS]):
        if isinstance(item, str):
            item = {"character": item}
        if not isinstance(item, dict):
            raise ValueError("scene actor must be an object or character id")
        actor_id = str(item.get("id") or chr(65 + index)).strip()
        character = str(item.get("character") or "default").strip() or "default"
        if not actor_id:
            raise ValueError("scene actor id must not be empty")
        if actor_id in seen:
            raise ValueError(f"duplicate scene actor id: {actor_id!r}")
        seen.add(actor_id)
        actors.append({"id": actor_id, "character": character})
    if not actors:
        raise ValueError("scene requires at least one actor")

    return {
        "world_id": world_id,
        "gain": gain,
        "cap": cap,
        "hop_decay": hop_decay,
        "max_hops": max_hops,
        "actors": actors,
        "proactive": ProactiveConfig.from_mapping(
            raw.get("proactive"),
        ).to_dict(),
    }


def _scene_signature(config):
    return json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)


def _scene_store():
    store = _scene_state.get("store")
    if store is None:
        store = PeerRelationshipStore(
            _scene_state.get("db_path") or DEFAULT_SOCIAL_DB_PATH,
        )
        _scene_state["store"] = store
    return store


def _close_scene():
    store = _scene_state.get("store")
    if store is not None:
        try:
            store.close()
        except Exception:
            pass
    _scene_state["store"] = None
    _scene_state["scene"] = None
    _scene_state["signature"] = None


def _build_scene_state(config, *, store=None):
    config = _scene_config(config)
    store = store or _scene_store()
    actors = []
    for raw in config["actors"]:
        engine, name, resolved_id = _contagion_engine(raw["character"])
        actors.append(SceneActor(
            actor_id=raw["id"],
            name=name,
            engine=engine,
            character_id=resolved_id,
        ))
    scene = FriendScene(
        actors,
        world_id=config["world_id"],
        store=store,
        gain=config["gain"],
        cap=config["cap"],
        hop_decay=config["hop_decay"],
        max_hops=config["max_hops"],
        proactive_config=config.get("proactive"),
    )
    _scene_state.update({
        "signature": _scene_signature(config),
        "config": config,
        "scene": scene,
        "turn_log": [],
        "last_payload": None,
    })
    return _scene_payload()


def _scene_payload():
    scene = _scene_state.get("scene")
    if scene is None:
        return {"initialized": False, "config": _scene_default_config()}
    payload = scene.snapshot()
    payload["initialized"] = True
    payload["config"] = _scene_state.get("config")
    payload["turn_log"] = list(_scene_state.get("turn_log", []))[-SCENE_HISTORY_LIMIT:]
    return payload


def _scene_ensure(config=None):
    current = _scene_state.get("scene")
    if config is not None:
        normalized = _scene_config(config)
        if current is None or _scene_signature(normalized) != _scene_state.get("signature"):
            _build_scene_state(normalized)
        return
    if current is None:
        _build_scene_state(_scene_default_config())


def _scene_reset(config=None, *, reset_relationships=False, store=None):
    config = _scene_config(config or _scene_default_config())
    store = store or _scene_store()
    if reset_relationships:
        store.delete_world(config["world_id"])
    _scene_state["turn_log"] = []
    return _build_scene_state(config, store=store)


def _scene_appraisal_from_payload(payload):
    raw = payload.get("appraisal")
    if isinstance(raw, dict):
        values = {}
        for name, (lo, hi) in APPRAISAL_LIMITS.items():
            if name in raw:
                values[name] = _contagion_float(
                    raw[name], 0.0, lo, hi,
                )
        return Appraisal(**values)
    text = str(payload.get("text") or "").strip()
    try:
        return analyze_text(text).appraisal
    except Exception:
        return classify_generic(text)


def _scene_now(payload):
    raw = payload.get("now")
    if raw is None:
        return time.time()
    return _contagion_float(raw, time.time(), 0.0, 1e12)


def _scene_proactive_text(candidate):
    reasons = set(candidate.reasons)
    if "longing" in reasons:
        return "突然有点想你，最近还好吗？"
    if "absence" in reasons:
        return "好久没听到你的消息了，今天过得怎么样？"
    if "love" in reasons:
        return "今天忽然想和你说说话，你那边还好吗？"
    if "trust" in reasons or "familiarity" in reasons:
        return "想起你了，来问问你最近怎么样。"
    return "在吗？突然想找你聊聊。"


def _scene_proactive(payload):
    """检查主动联系意愿；可选地让一个角色发出一条真实场景回合。"""
    payload = payload if isinstance(payload, dict) else {}
    _scene_ensure(payload.get("config"))
    scene = _scene_state.get("scene")
    if scene is None:
        raise ValueError("scene is not initialized")

    action = str(payload.get("action") or PROACTIVE_ACTION_CHECK).strip()
    if action not in (PROACTIVE_ACTION_CHECK, PROACTIVE_ACTION_SEND):
        raise ValueError(f"unknown proactive action: {action!r}")
    now = _scene_now(payload)
    requested = str(payload.get("actor_id") or "").strip() or None
    if requested is not None and requested not in scene.actors:
        raise ValueError(f"unknown scene actor: {requested!r}")

    chosen = scene.proactive.choose(now=now)
    if action == PROACTIVE_ACTION_SEND and requested is not None:
        chosen = scene.proactive.assess(requested, now=now)
        if not chosen.eligible:
            raise ValueError(
                f"actor {requested!r} cannot contact now: "
                f"{chosen.blocked_reason}"
            )

    result = {
        "action": action,
        "now": now,
        "chosen": chosen.to_dict() if chosen is not None else None,
        "candidates": [
            item.to_dict()
            for item in scene.proactive.evaluate(now=now)
        ],
        "proactive": scene.proactive.snapshot(now=now),
        "config": _scene_state.get("config"),
    }
    if action == PROACTIVE_ACTION_CHECK:
        return result
    if chosen is None:
        result["sent"] = False
        result["reason"] = "no_eligible_actor"
        return result

    text = _scene_proactive_text(chosen)
    turn_payload = _scene_turn({
        "speaker_id": chosen.actor_id,
        "text": text,
        "now": now,
    })
    receipt = scene.proactive.mark_contact(chosen, now=now)
    turn_payload["sent"] = True
    turn_payload["proactive_send"] = {
        "actor_id": chosen.actor_id,
        "name": scene.actors[chosen.actor_id].name,
        "text": text,
        "candidate": chosen.to_dict(),
        "receipt": receipt,
        "snapshot": scene.proactive.snapshot(now=now),
    }
    return turn_payload


def _scene_actor_payload(actor, result):
    return {
        "actor_id": actor.actor_id,
        "name": actor.name,
        "character_id": actor.character_id,
        "utterance": result.get("utterance", ""),
        "action": result.get("action", "approach"),
        "dominant": result.get("_dominant", "neutral"),
        "state": result.get("_state", {}),
        "felt": {
            key: round(float(value), 6)
            for key, value in actor.engine.state.felt_all().items()
        },
        "contagion_bias": result.get("contagion_bias", {}),
        "zone": result.get("tolerance", {}).get("zone", "regulated"),
        "needs_llm": bool(result.get("needs_llm", False)),
    }


def _scene_turn(payload):
    payload = payload if isinstance(payload, dict) else {}
    _scene_ensure(payload.get("config"))
    scene = _scene_state["scene"]
    if scene is None:
        raise ValueError("scene is not initialized")

    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("empty scene text")
    if len(text) > SCENE_MAX_TEXT:
        raise ValueError(f"scene text is too long (max {SCENE_MAX_TEXT})")

    speaker_id = str(payload.get("speaker_id") or "user").strip() or "user"
    if speaker_id != "user" and speaker_id not in scene.actors:
        raise ValueError(f"unknown scene speaker: {speaker_id!r}")

    appraisal = _scene_appraisal_from_payload(payload)
    relevance = payload.get("relevance")
    relevance = relevance if isinstance(relevance, dict) else None
    boundary_violations = payload.get("boundary_violations")
    boundary_violations = (
        boundary_violations if isinstance(boundary_violations, dict) else {}
    )
    biases = scene.biases_for(
        speaker_id, appraisal, relevance=relevance,
    ) if speaker_id in scene.actors else {}

    appraisals = {
        actor_id: appraisal for actor_id in scene.actors
    }
    actors_payload = {}
    for actor_id, actor in scene.actors.items():
        result = respond(
            actor.engine,
            text,
            appraisal=appraisal,
            character_id=actor.character_id,
            boundary_violation=bool(boundary_violations.get(actor_id, False)),
            contagion_bias=biases.get(actor_id),
        )
        actors_payload[actor_id] = _scene_actor_payload(actor, result)

    turn = scene.record_turn(
        speaker_id,
        appraisals,
        boundary_violations=boundary_violations,
        now=_scene_now(payload),
    )
    result = {
        "turn": turn["turn"],
        "source": speaker_id,
        "text": text,
        "appraisal": appraisal.to_dict(),
        "biases": biases,
        "actors": actors_payload,
        "relationship_changes": turn["relationship_changes"],
        "scene": scene.snapshot(),
        "config": _scene_state.get("config"),
    }
    log = _scene_state.setdefault("turn_log", [])
    log.append({
        "turn": result["turn"],
        "source": speaker_id,
        "text": text,
        "biases": biases,
        "utterances": {
            actor_id: item["utterance"]
            for actor_id, item in actors_payload.items()
        },
        "relationship_changes": turn["relationship_changes"],
    })
    if len(log) > SCENE_HISTORY_LIMIT:
        del log[:-SCENE_HISTORY_LIMIT]
    _scene_state["last_payload"] = result
    return result


# ═══════════════════════════════════════════════
# HTTP 路由
# ═══════════════════════════════════════════════

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
CONTAGION_HTML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "contagion.html",
)
SCENE_HTML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "scene.html",
)


class DashboardHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._do_GET_impl()
        except Exception as e:
            import traceback
            import sys
            print(f"\n[ERROR] do_GET: {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            try:
                self.send_error(500, str(e))
            except Exception:
                pass  # 发不动就算了

    def _do_GET_impl(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        if path == "/api/state":
            with _lock:
                data = build_state_response()
            hist = _state["history"]
            hist.append(tolerance_history_point(data))
            if len(hist) > MAX_HISTORY:
                hist.pop(0)
            self._send_json(data)

        elif path == "/api/history":
            count = int(qs.get("n", [120])[0])
            with _lock:
                h = list(_state["history"][-count:]) if len(_state["history"]) > count else list(_state["history"])
            self._send_json({"history": h, "character": _state["char_name"]})

        elif path == "/api/character":
            self._send_json({
                "character": _state["char_name"],
                "character_id": _state["char_id"],
                "has_appraise_fn": _state["appraise_fn"] is not None,
            })

        elif path == "/api/tts":
            text = qs.get("text", [""])[0]
            if not text:
                self._send_json({"error": "missing text"}, 400)
                return
            try:
                import urllib.request as ureq
                tts_req = ureq.Request(
                    f"{SOVITS_URL}?text={ureq.quote(text)}&text_lang=zh"
                )
                with ureq.urlopen(tts_req, timeout=30) as tts_resp:
                    audio = tts_resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
            except Exception as e:
                self._send_json({"error": f"TTS failed: {e}"}, 500)

        elif path == "/api/characters":
            self._send_json({"characters": get_all_characters()})

        elif path == "/api/contagion/state":
            with _contagion_lock:
                if not _contagion_state["engines"]:
                    self._send_json({"initialized": False})
                else:
                    self._send_json(_contagion_payload())

        elif path == "/api/scene/state":
            with _scene_lock:
                _scene_ensure()
                self._send_json(_scene_payload())

        elif path == "/api/chat-log":
            count = int(qs.get("n", [100])[0])
            self._send_json({"messages": load_chat_log(count)})

        elif path == "/api/reload":
            chars = get_all_characters()
            self._send_json({"message": f"发现 {len(chars)} 个角色", "characters": chars})

        elif path == "/tts_test" or path == "/tts_test.html":
            tts_html = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_test.html")
            try:
                with open(tts_html, "r", encoding="utf-8") as f:
                    html = f.read()
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_error(404)

        elif path == "/contagion" or path == "/contagion.html":
            try:
                with open(CONTAGION_HTML_PATH, "r", encoding="utf-8") as f:
                    html = f.read()
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_error(404, "contagion HTML not found")

        elif path == "/scene" or path == "/scene.html":
            try:
                with open(SCENE_HTML_PATH, "r", encoding="utf-8") as f:
                    html = f.read()
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_error(404, "scene HTML not found")

        elif path == "/" or path == "/index.html":
            try:
                with open(HTML_PATH, "r", encoding="utf-8") as f:
                    html = f.read()
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_error(404, "dashboard HTML not found")

        else:
            self.send_error(404)

    def do_POST(self):
        try:
            self._do_POST_impl()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, str(e))

    def _do_POST_impl(self):
        path = urlparse(self.path).path

        if path.startswith("/api/scene/"):
            content_length = int(self.headers.get("Content-Length", 0) or 0)
            raw_body = self.rfile.read(content_length).decode(
                "utf-8", errors="replace",
            ) if content_length else ""
            try:
                payload = json.loads(raw_body) if raw_body else {}
            except json.JSONDecodeError:
                self._send_json({"error": "invalid json"}, 400)
                return
            if not isinstance(payload, dict):
                self._send_json({"error": "payload must be an object"}, 400)
                return

            try:
                if path == "/api/scene/reset":
                    config = payload.get("config")
                    if config is None:
                        config = {
                            key: value for key, value in payload.items()
                            if key != "reset_relationships"
                        }
                    with _scene_lock:
                        self._send_json(_scene_reset(
                            config,
                            reset_relationships=bool(
                                payload.get("reset_relationships", False)
                            ),
                        ))
                    return

                if path == "/api/scene/turn":
                    with _scene_lock:
                        self._send_json(_scene_turn(payload))
                    return

                if path == "/api/scene/proactive":
                    with _scene_lock:
                        self._send_json(_scene_proactive(payload))
                    return

                self.send_error(404)
            except (TypeError, ValueError, KeyError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return

        if path.startswith("/api/contagion/"):
            content_length = int(self.headers.get("Content-Length", 0) or 0)
            raw_body = self.rfile.read(content_length).decode(
                "utf-8", errors="replace",
            ) if content_length else ""
            try:
                payload = json.loads(raw_body) if raw_body else {}
            except json.JSONDecodeError:
                self._send_json({"error": "invalid json"}, 400)
                return
            if not isinstance(payload, dict):
                self._send_json({"error": "payload must be an object"}, 400)
                return

            try:
                if path == "/api/contagion/appraise":
                    text = str(payload.get("text", "")).strip()
                    if not text:
                        self._send_json({"error": "empty text"}, 400)
                        return
                    result = analyze_text(text)
                    self._send_json(result.to_dict())
                    return

                if path == "/api/contagion/reset":
                    config = payload.get("config", payload)
                    with _contagion_lock:
                        self._send_json(_contagion_reset(config))
                    return

                if path == "/api/contagion/tick":
                    config = payload.get("config")
                    with _contagion_lock:
                        self._send_json(_run_contagion_tick(
                            config,
                            payload.get("appraisals"),
                            payload.get("steps", 1),
                        ))
                    return

                self.send_error(404)
            except (TypeError, ValueError, KeyError) as exc:
                self._send_json({"error": str(exc)}, 400)
            return

        if path == "/api/event":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                payload = {"text": raw_body}

            event_text = payload.get("text", "").strip()
            if not event_text:
                self._send_json({"error": "empty event"}, 400)
                return

            with _lock:
                engine = _state["engine"]
                appraise_fn = _state["appraise_fn"]
                # 优先用角色专属 appraise，失败回退到通用分类
                appraisal = classify_generic(event_text)
                if appraise_fn:
                    try:
                        appraisal = appraise_fn(event_text)
                    except Exception:
                        pass  # 角色不识别这个事件，用通用分类

                # 低信任+亲密称呼 → 越界，触发生理厌恶+氛围紧绷
                boundary = False
                if engine.state.trust < 0.5:
                    intimate = ["老公","老婆","亲爱的","宝贝","我爱你","喜欢你","娶","嫁","吻","抱"]
                    if any(w in event_text for w in intimate):
                        boundary = True

                # L4：所有持久记忆只走关系域存储，按角色 × 用户隔离。
                # 本地模板路径手动应用先验并记录；LLM 路径交给 respond_llm。
                memory_service = _get_memory_service()
                memory_prior = _memory_appraisal_prior()

                # 自定义角色自动走 LLM，否则看前端是否传了 api_key
                api_key = payload.get("api_key", "").strip()
                is_custom = _state["char_id"] == "custom"
                if is_custom and not api_key:
                    api_key = _state.get("custom_api_key", "")
                if api_key:
                    char_desc = _state.get("custom_desc", "") or "A person with complex emotions."
                    char_name = _state["char_name"]
                    # 不猜 key 类型——Kimi 和 DeepSeek 都试，哪个通用哪个
                    local = respond_llm(engine, event_text,
                        appraisal=appraisal,
                        character_id=_state["char_id"],
                        character_name=char_name,
                        character_desc=char_desc,
                        api_key=api_key,
                        base_url="https://api.moonshot.cn/v1",
                        model="moonshot-v1-8k",
                        fallback_key=api_key,
                        fallback_url="https://api.deepseek.com/v1",
                        fallback_model="deepseek-chat",
                        relationship_memory=memory_service,
                        user_id=_current_user_id(),
                        boundary_violation=boundary)
                else:
                    local = respond(
                        engine, event_text,
                        appraisal=appraisal,
                        character_id=_state["char_id"],
                        memory_appraisal_bias=memory_prior,
                        boundary_violation=boundary,
                    )
                    if memory_service is not None:
                        _remember_turn(
                            engine, event_text,
                            local.get("utterance", ""), appraisal,
                        )

            resp = {
                "utterance": local.get("utterance", ""),
                "_llm_used": local.get("_llm_used", False),
                "_llm_backend": local.get("_llm_backend", ""),
                "state": build_state_response(),
            }
            if _state.get("memory_last_error"):
                resp["_memory_error"] = _state["memory_last_error"]
            self._send_json(resp)
            # 自动存档 + 聊天记录
            threading.Thread(target=save_to_disk, daemon=True).start()
            threading.Thread(target=lambda: save_chat_message(
                _state["char_name"], event_text, resp.get("utterance", "")
            ), daemon=True).start()

        elif path == "/api/replay":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                payload = {"text": raw_body}

            event_text = str(payload.get("text", "")).strip()
            appraisal_data = payload.get("appraisal")
            if appraisal_data is not None and not isinstance(appraisal_data, dict):
                self._send_json({"error": "appraisal must be an object"}, 400)
                return
            if not event_text and appraisal_data is None:
                self._send_json({"error": "empty event"}, 400)
                return

            try:
                appraisal = (
                    make_appraisal(appraisal_data)
                    if appraisal_data is not None
                    else analyze_text(event_text).appraisal
                )
            except Exception as exc:
                self._send_json({"error": f"bad appraisal: {exc}"}, 400)
                return

            with _lock:
                source = _state["engine"]
                # 只读回放：复制状态与耐受窗，不碰真实记忆库/快照。
                replay_engine = copy.copy(source)
                replay_engine.state = copy.deepcopy(source.state)
                replay_engine.tolerance = copy.deepcopy(source.tolerance)
                replay_engine.scars = copy.deepcopy(source.scars)
                replay_engine._recent_shocks = list(source._recent_shocks)
                replay_engine.memory = MemoryStore()
                boundary_value = payload.get("boundary_violation")
                boundary = (
                    bool(boundary_value) if boundary_value is not None
                    else detect_boundary(
                        event_text, trust=source.state.trust,
                    ).violated if event_text else False
                )
                local = respond(
                    replay_engine, event_text,
                    appraisal=appraisal,
                    character_id=_state["char_id"],
                    boundary_violation=boundary,
                    trace=True,
                )
                replay_state = dict(replay_engine.state.to_dict())

            self._send_json({
                "replay": True,
                "boundary_violation": boundary,
                "trace": local.get("trace"),
                "state": replay_state,
                "expression": local.get("expression"),
                "action": local.get("action"),
                "utterance": local.get("utterance", ""),
            })

        elif path == "/api/reset":
            char_id = _state["char_id"]
            with _lock:
                new_engine, new_fn, new_name, new_id = (
                    load_character(char_id) if char_id != "default"
                    else _default_character()
                )
                _state["engine"] = new_engine
                _state["appraise_fn"] = new_fn
                _state["char_name"] = new_name
                _state["char_id"] = new_id
                _state["memory_id"] = _memory_character_id(new_id, new_name)
                _state["history"].clear()
                _state["custom_desc"] = _BUILTIN_DESCS.get(new_id, "")
            delete_save()
            self._send_json({"message": "reset", "state": build_state_response()})

        elif path == "/api/wake":
            with _lock:
                result = _state["engine"].wake()
            self._send_json({"wake": result, "state": build_state_response()})

        # 切换角色
        elif path == "/api/switch":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                payload = {"character": raw_body.strip()}
            new_char = payload.get("character", "").strip()

            if not new_char:
                self._send_json({"error": "missing character id"}, 400)
                return

            with _lock:
                # 先查自定义角色
                custom_chars = load_custom_chars()
                if new_char in custom_chars:
                    c = custom_chars[new_char]
                    pers = Personality(
                        openness=float(c.get("O",0.5)), conscientiousness=float(c.get("C",0.5)),
                        extraversion=float(c.get("E",0.5)), agreeableness=float(c.get("A",0.5)),
                        neuroticism=float(c.get("N",0.5)))
                    engine = EmotionEngine(state=EmotionalState(), personality=pers,
                                           memory=MemoryStore(), scars=SensitizationStore())
                    new_id, new_name, new_fn = new_char, c.get("name", new_char), None
                    _state["custom_desc"] = c.get("desc", "")
                    _state["custom_api_key"] = c.get("api_key", "")
                else:
                    new_engine, new_fn, new_name, new_id = load_character(new_char)
                    engine = new_engine
                    _state["custom_desc"] = _BUILTIN_DESCS.get(new_id, "")
                    _state["custom_api_key"] = ""

                _state["engine"] = engine
                _state["appraise_fn"] = new_fn
                _state["char_name"] = new_name
                _state["char_id"] = new_id
                _state["memory_id"] = _memory_character_id(new_id, new_name)
                _state["history"].clear()

            self._send_json({
                "message": f"switched to {new_name}",
                "character": new_name,
                "character_id": new_id,
                "state": build_state_response(),
            })

        # 保存自定义角色到磁盘
        elif path == "/api/save-character":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                self._send_json({"error": "invalid json"}, 400)
                return

            char_id = payload.get("id", "").strip()
            if not char_id:
                self._send_json({"error": "missing id"}, 400)
                return

            chars = load_custom_chars()
            chars[char_id] = {
                "name": payload.get("name", char_id).strip() or char_id,
                "desc": payload.get("desc", "").strip(),
                "api_key": payload.get("api_key", "").strip(),
                "O": float(payload.get("O", 0.5)),
                "C": float(payload.get("C", 0.5)),
                "E": float(payload.get("E", 0.5)),
                "A": float(payload.get("A", 0.5)),
                "N": float(payload.get("N", 0.5)),
            }
            save_custom_chars(chars)
            self._send_json({"message": f"saved {char_id}", "characters": get_all_characters()})

        # 删除自定义角色
        elif path == "/api/delete-character":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                payload = {"id": raw_body.strip()}
            char_id = payload.get("id", "").strip()
            if char_id:
                chars = load_custom_chars()
                if char_id in chars:
                    del chars[char_id]
                    save_custom_chars(chars)
            self._send_json({"message": f"deleted {char_id}", "characters": get_all_characters()})

        # 自定义角色：OCEAN + 描述 → 实时创建引擎实例（不存盘）
        elif path == "/api/custom":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                self._send_json({"error": "invalid json"}, 400)
                return

            name = payload.get("name", "Custom").strip() or "Custom"
            desc = payload.get("desc", "").strip() or "A unique character."
            ocean = payload.get("ocean", {})
            api_key = payload.get("api_key", "").strip()

            pers = Personality(
                openness          = float(ocean.get("O", 0.5)),
                conscientiousness = float(ocean.get("C", 0.5)),
                extraversion      = float(ocean.get("E", 0.5)),
                agreeableness     = float(ocean.get("A", 0.5)),
                neuroticism       = float(ocean.get("N", 0.5)),
            )
            engine = EmotionEngine(
                state=EmotionalState(), personality=pers,
                memory=MemoryStore(), scars=SensitizationStore()
            )

            with _lock:
                _state["engine"] = engine
                _state["appraise_fn"] = None  # 自定义角色没有 eval trigger 映射
                _state["char_name"] = name
                _state["char_id"] = "custom"
                _state["memory_id"] = _memory_character_id("custom", name)
                _state["history"].clear()
                # 存储自定义描述+api_key 供后续 respond_llm 使用
                _state["custom_desc"] = desc
                _state["custom_api_key"] = api_key

            self._send_json({
                "message": f"created {name}",
                "character": name,
                "character_id": "custom",
                "state": build_state_response(),
            })

        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ═══════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Emotion Engine 仪表盘")
    parser.add_argument("--char", default="default", help="角色 ID（default/furina/kokomi/columbina）")
    parser.add_argument("--port", type=int, default=9020, help="HTTP 端口（默认 9020）")
    parser.add_argument("--fresh", action="store_true", help="忽略存档，全新开始")
    parser.add_argument("--no-save", action="store_true", help="不自动存档")
    parser.add_argument(
        "--memory-db", default=DEFAULT_MEMORY_DB_PATH,
        help="关系域记忆 SQLite 路径（默认 dashboard/_relationship_memory.db）",
    )
    parser.add_argument(
        "--user-id", default=DEFAULT_USER_ID,
        help="记忆归属用户 ID（默认 dashboard）",
    )
    parser.add_argument(
        "--social-db", default=DEFAULT_SOCIAL_DB_PATH,
        help="角色间关系 SQLite 路径（默认 dashboard/_social_relationships.db）",
    )
    args = parser.parse_args()

    # 尝试读档
    if not args.fresh:
        restored = load_from_disk()
        if restored:
            engine, appraise_fn, char_name, char_id = restored
        else:
            engine, appraise_fn, char_name, char_id = load_character(args.char)
    else:
        engine, appraise_fn, char_name, char_id = load_character(args.char)
        delete_save()
    _state["engine"] = engine
    _state["appraise_fn"] = appraise_fn
    _state["char_name"] = char_name
    _state["char_id"] = char_id
    _state["memory_id"] = _memory_character_id(char_id, char_name)
    _state["user_id"] = args.user_id.strip() or DEFAULT_USER_ID
    _state["memory_db_path"] = args.memory_db
    _scene_state["db_path"] = args.social_db
    _state["custom_desc"] = _BUILTIN_DESCS.get(char_id, "")

    # 提前建库，把配置错误暴露在启动日志里；失败时仍可本地运行。
    memory_service = _get_memory_service()

    port = args.port
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    global _no_save
    _no_save = args.no_save
    print(f"\n{'='*50}")
    print("  Emotion Engine 仪表盘")
    print(f"  http://localhost:{port}")
    print(f"{'='*50}")
    print(f"  角色: {char_name} ({char_id})")
    print(f"  存档: {'关闭' if _no_save else SAVE_PATH}")
    print(f"  记忆: {_state['memory_db_path']} · {_current_memory_id()} × {_current_user_id()}")
    print(f"  社交: {_scene_state['db_path']} · http://localhost:{port}/scene")
    if memory_service is None:
        print(f"  [!] 关系域记忆不可用: {_state.get('memory_last_error', 'unknown')}")
    print("  Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n仪表盘已停止。")
        server.server_close()
    finally:
        _close_memory_service()
        _close_scene()


if __name__ == "__main__":
    main()
