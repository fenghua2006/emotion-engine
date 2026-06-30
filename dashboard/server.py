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

import sys, os, json, time, threading, argparse, importlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import (
    EmotionEngine, EmotionalState, Personality, Appraisal, Channel,
    MemoryStoreDB, SensitizationStore, _dominant_channel, respond, respond_llm
)

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
        memory=MemoryStoreDB(), scars=SensitizationStore()
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
SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_emotion_save.json")
CHARS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_custom_chars.json")
CHAT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_chat_log.json")
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
        "custom_desc": _state.get("custom_desc", ""),
        "custom_api_key": _state.get("custom_api_key", ""),
        "atmosphere": engine.atmosphere,
        "state": engine.state.to_dict(),
        "last_update": engine.state._last_update,
        "events_today": engine.events_today,
        "positive_events": engine.positive_events,
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

    # 结算离线时间
    engine.wake()

    print(f"📂 存档已恢复 — {char_name}，离线 {round((time.time()-engine.state._last_update)/3600,1)}h")
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
    "history": [],       # 历史数据点
    "custom_desc": "",   # 角色描述（LLM prompt 用）
}

# 内置角色描述——确保 LLM 拿到正确人设
_BUILTIN_DESCS = {
    "furina": "你是芙宁娜·德·枫丹，前水神，以人类之躯扮演神明500年。外层浮夸自信戏剧化，内层疲惫孤独。回复自然口语化，一两句话。",
    "kokomi": "你是珊瑚宫心海，海祇岛现人神巫女，天才军师。内向但睿智，社交场合容易疲惫。说话温柔精准。",
    "columbina": "你是哥伦比娅，孤独的月神。沉默寡言，语句稀疏意味深长。情感淡漠却不冷酷。",
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
        "dominant": dom or "neutral",
        "dominant_value": round(getattr(s, dom.replace("_dominant", ""), 0), 3) if dom and dom.replace("_dominant", "") in s.to_dict() else 0,
        "shock_channels": [],
        "scars": [{"tag": s.tag, "count": s.trigger_count, "shift": round(s.threshold_shift, 3)}
                  for s in scars] if scars else [],
        "memory": {
            "flash": _count_memories(engine, "flash"),
            "long_term": _count_memories(engine, "long_term"),
            "short_term": _count_memories(engine, "short_term"),
        },
        "needs_llm": s.surprise > 0.8 or len(scars) > 4,
    }


def _count_memories(engine, tier: str) -> int:
    try:
        return engine.memory.db.execute(
            "SELECT COUNT(*) FROM memories WHERE tier=?", (tier,)
        ).fetchone()[0]
    except Exception:
        return 0


# ═══════════════════════════════════════════════
# HTTP 路由
# ═══════════════════════════════════════════════

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


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
            import traceback, sys
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
            hist.append({
                "t": data["timestamp"],
                "felt": data["felt"],
                "atmosphere": data["atmosphere"],
            })
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
                result = engine.tick(appraisal, boundary_violation=boundary)

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
                        character_id=_state["char_id"],
                        character_name=char_name,
                        character_desc=char_desc,
                        api_key=api_key,
                        base_url="https://api.moonshot.cn/v1",
                        model="moonshot-v1-8k",
                        fallback_key=api_key,
                        fallback_url="https://api.deepseek.com/v1",
                        fallback_model="deepseek-chat")
                else:
                    local = respond(engine, event_text, character_id=_state["char_id"])

            resp = {
                "utterance": local.get("utterance", ""),
                "_llm_used": local.get("_llm_used", False),
                "_llm_backend": local.get("_llm_backend", ""),
                "state": build_state_response(),
            }
            self._send_json(resp)
            # 自动存档 + 聊天记录
            threading.Thread(target=save_to_disk, daemon=True).start()
            threading.Thread(target=lambda: save_chat_message(
                _state["char_name"], event_text, resp.get("utterance", "")
            ), daemon=True).start()

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
                                           memory=MemoryStoreDB(), scars=SensitizationStore())
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
                memory=MemoryStoreDB(), scars=SensitizationStore()
            )

            with _lock:
                _state["engine"] = engine
                _state["appraise_fn"] = None  # 自定义角色没有 eval trigger 映射
                _state["char_name"] = name
                _state["char_id"] = "custom"
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
    _state["custom_desc"] = _BUILTIN_DESCS.get(char_id, "")

    port = args.port
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    global _no_save
    _no_save = args.no_save
    print(f"\n{'='*50}")
    print(f"  Emotion Engine 仪表盘")
    print(f"  http://localhost:{port}")
    print(f"{'='*50}")
    print(f"  角色: {char_name} ({char_id})")
    print(f"  存档: {'关闭' if _no_save else SAVE_PATH}")
    print(f"  Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n仪表盘已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
