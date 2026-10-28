# L8.5：主动联系 / 调度（v0）

> 代码：`proactive.py` · 集成：`social.FriendScene` · 网页：`/scene` 的“主动联系”卡片
> 测试：`test_l85_proactive.py` + `test_proactive_dashboard.py`
> 前置：L8 朋友圈场景（`social.py`）

L8.5 回答一个 L8 v0 没有回答的问题：

> 没有人先开口时，哪个角色会主动来找用户？什么时候？

它是一层**引擎外的确定性调度**：只读角色当前情绪与关系，不推进 `tick()`、
不写记忆、不写人格，也不新增 golden。真正发送时，才走一次普通场景回合。

---

## 1. 分层与硬边界

```text
engine.py（单角色动力学）
  └── social.py（L8：有向关系 + 场景回合）
        └── proactive.py（L8.5：意愿评估 + 调度状态）
              └── dashboard/server.py + scene.html（/scene 主动联系卡片）
```

`proactive.py`：

- 不导入 `engine.py` / `social.py` / `contagion.py`；
- 通过 duck typing 读取 `actor.engine.state`（`longing` / `love` / `trust`）；
- 不修改 `EmotionalState`、`Personality`、`CharacterCore` 或关系快照；
- 不进入 `tick()`，默认单角色路径不导入本模块；
- 只依赖标准库。

`FriendScene` 持有 `self.proactive = ProactiveScheduler(...)`，
`record_turn()` 会调用 `observe_turn()` 记录发言；`snapshot()` 增加
`proactive` 字段。调度状态只在内存中，场景重启 / 重建后归零；关系仍然持久化。

---

## 2. 紧迫度

对每个角色 A：

```text
longing       = clamp(felt(longing), 0, 1)
love_norm     = clamp(raw_love / 2, 0, 1)
trust_norm    = clamp(raw_trust, 0, 1)
absence       = clamp(elapsed / silence_window, 0, 1)
familiarity   = 目标为角色时取关系 familiarity，否则 0
tension       = 目标为角色时取关系 tension，否则 0
pending       = 当前未回复的主动消息数

urgency = 0.35 × longing
        + 0.25 × absence
        + 0.20 × love_norm
        + 0.10 × trust_norm
        + 0.10 × familiarity
        - 0.25 × tension
        - 0.20 × pending
```

`urgency` 最终截断到 `[0, 1]`。`reasons` 按各分量从大到小列出正向贡献
（`> 0.02`），供网页显示；负项显示在 `components` 中。

说明：

- `longing` 使用 `felt_all()` 的感知压缩值，和表达层口径一致；
- `love` / `trust` 用原始通道值，因为它们是慢通道，已经足够平滑；
- 面向用户时没有角色关系，`familiarity` / `tension` 为 0；
- 调度器可以面向另一个角色评估，但当前 `/scene` 只对用户使用。

---

## 3. 门控顺序

一个角色只有在所有门都通过时才 `eligible`。顺序固定：

1. `quiet_hours`：本地时间落在安静时段；
2. `min_silence`：距离上次场景活动不足 `min_silence_seconds`；
3. `cooldown`：距离该角色上次主动联系不足 `cooldown_seconds`；
4. `daily_limit`：该角色今天已达 `max_per_day`；
5. `unanswered_limit`：该角色未回复的主动消息已达 `max_unanswered`；
6. `below_threshold`：`urgency < threshold`；
7. 全部通过：`eligible=True`，`blocked_reason=""`。

安静时段支持跨午夜：`23 → 7` 表示 `hour >= 23` 或 `hour < 7`。
`quiet_start_hour` 与 `quiet_end_hour` 任一为 `None`，或两者相等，即关闭安静时段。

---

## 4. 调度状态

`ProactiveScheduler` 在内存中维护：

| 状态 | 含义 |
|---|---|
| `last_activity_at` | 最近一次任何场景发言 / 主动发送 |
| `last_user_at` | 最近一次用户发言 |
| `last_speaker` | 最近一次发言者 |
| `last_actor_at` | 每个角色最近一次发言 |
| `last_contact_at` | 每个角色最近一次主动联系 |
| `pending` | 每个角色已发但用户未回复的主动消息数 |
| `daily_counts` | 按本地日期和角色统计的主动次数 |
| `total_contacts` | 进程内累计主动次数 |

规则：

- `observe_turn("user")` 更新活动时间，并把**所有角色的 `pending` 清零**；
- `observe_turn(actor_id)` 只更新活动时间，不清除 `pending`；
- `mark_contact()` 在**真正发送成功后**调用，增加 `pending` 与当日计数；
- `mark_contact()` 不调用 `tick()`，不改引擎状态、人格或关系；
- `reset()` / 场景重建清空调度状态，但不清关系；
- `FriendScene.record_turn()` 统一调用 `observe_turn()`，因此普通发言与
  主动发送共享同一套“活动时间”口径。

---

## 5. 发送流程

`/scene` 的发送不是另一套对话系统，而是：

```text
choose / assess
  → 生成确定性台词
  → 走一次普通 _scene_turn(speaker=角色)
  → mark_contact
  → 返回本回合结果 + 主动联系回执
```

因此主动发送会：

- 让所有角色各 `respond()` 一次（发送者也推进一次自己的情绪）；
- 按 L7 关系边产生感染偏置；
- 按 L8 规则更新听者 → 发送者的关系；
- 写入场景回合日志；
- 增加发送者的 `pending` 与当日主动次数。

它不会：

- 额外调用一次 `tick()`；
- 绕过用户发言的正常回合路径；
- 直接把消息写进记忆库；
- 在用户未回复时反复无视上限发送。

台词是模板，不调用 LLM。当前模板按 `reasons` 选择：

| 首要信号 | 台词 |
|---|---|
| `longing` | 突然有点想你，最近还好吗？ |
| `absence` | 好久没听到你的消息了，今天过得怎么样？ |
| `love` | 今天忽然想和你说说话，你那边还好吗？ |
| `trust` / `familiarity` | 想起你了，来问问你最近怎么样。 |
| 其他 | 在吗？突然想找你聊聊。 |

---

## 6. 配置

`ProactiveConfig` 默认值：

| 字段 | 默认 | 说明 |
|---|---:|---|
| `min_silence_seconds` | 1800 | 至少安静 30 分钟 |
| `silence_window_seconds` | 10800 | 3 小时达到 absence=1 |
| `cooldown_seconds` | 21600 | 同一角色 6 小时冷却 |
| `max_per_day` | 2 | 每个角色每日最多 2 次 |
| `max_unanswered` | 2 | 未回复 2 次后停止 |
| `quiet_start_hour` | 23 | 安静时段开始 |
| `quiet_end_hour` | 7 | 安静时段结束 |
| `threshold` | 0.35 | 意愿门槛 |
| `longing_weight` | 0.35 | 思念权重 |
| `absence_weight` | 0.25 | 静默权重 |
| `love_weight` | 0.20 | 亲密权重 |
| `trust_weight` | 0.10 | 信任权重 |
| `familiarity_weight` | 0.10 | 熟悉权重 |
| `tension_weight` | 0.25 | 紧张惩罚 |
| `unanswered_weight` | 0.20 | 未回复惩罚 |

所有数值在构造时 clamp；非法时间回退默认，非法小时关闭对应安静边界。
`from_mapping()` 接受前端 / API 的字典，`to_dict()` 返回可 JSON 化的快照。

---

## 7. API

### `POST /api/scene/proactive`

检查：

```json
{"action": "check"}
```

发送（自动选择）：

```json
{"action": "send"}
```

发送（指定角色）：

```json
{"action": "send", "actor_id": "B"}
```

可选 `"now": <unix seconds>` 用于测试 / 回放；默认使用服务器当前时间。
未传 `config` 时使用当前场景；传入时先执行与 `/api/scene/turn` 相同的配置校验。

检查响应：

```text
action, now, chosen, candidates[], proactive{config, actors, pending, ...}, config
```

`candidates[]` 每项：

```text
actor_id, target_id, urgency, eligible, blocked_reason, reasons[],
components{}, elapsed_seconds, pending
```

发送成功响应是完整的 `/api/scene/turn` 响应，再加：

```text
sent, proactive_send{actor_id, name, text, candidate, receipt, snapshot}
```

没有可发送角色时，`send` 返回 HTTP 200：

```json
{"action": "send", "sent": false, "reason": "no_eligible_actor", ...}
```

指定角色被门控时返回 HTTP 400，错误信息包含 `blocked_reason`；
未知 action / actor 也返回 400。

`FriendScene.snapshot()` 与 `GET /api/scene/state` 都包含 `proactive`，
但只有发送动作会改变 `pending` / 当日计数。

---

## 8. 网页

`/scene` 新增“主动联系”卡片：

- “检查意愿”：只读评估，不 tick、不改关系；
- “发送给最想联系的人”：自动选择 urgency 最高的 eligible 角色；
- 可选指定角色；被门控时显示原因；
- 每个角色展示 urgency、可联系 / 被哪条门挡住、正向原因、未回复数；
- 配置面板可编辑 threshold、最小静默、absence window、冷却、每日上限、
  未回复上限与安静时段（分钟 / 小时在 UI 转换，API 仍用秒）。

---

## 9. 测试与验收

```bash
python -m unittest test_l85_proactive.py test_proactive_dashboard.py -v
```

- `test_l85_proactive.py`（17）：配置默认 / clamp、最小静默、高思念触发、
  安静时段跨午夜、冷却、每日上限、未回复上限与用户清零、标记发送不改引擎 /
  关系、候选选择、面向角色的 familiarity / tension、非法目标、快照结构、
  `FriendScene` 集成、用户回合清零、检查不 tick。
- `test_proactive_dashboard.py`（13）：默认配置、配置归一化、blocked 候选、
  自动选择最高 urgency、发送生成回合与回执、指定角色、被门控 400、
  非法 action / actor、用户回合清零、冷却、检查无副作用、重建清计数、
  页面控件冒烟。

验收目标：

```text
30 个 L8.5 tests（17 核心 + 13 仪表盘）, OK
全量 513 tests, OK
ruff check . -> All checks passed
coverage -> 含 proactive 后仍需 ≥ 80%（branch）
L1 / L2 / L5 六套 golden 无 diff
```

L8.5 不新增 golden：它不修改任何 `tick()` 公式，只读取状态并编排普通回合。

---

## 10. 已知限制

- **调度状态不持久化**：服务重启后冷却、每日计数、未回复计数归零；
  需要跨重启时应新增独立表，而不是塞进关系表。
- **没有后台定时器**：v0 由网页按钮触发检查 / 发送；真正“到点自动发”
  需要一个外部调度器调用同一 API，并把发送结果写入消息队列。
- **台词是模板**：不调用 LLM，因此主动消息的表达丰富度低于普通表达层。
- **对用户使用固定的 target_id="user"**：面向角色的调度已支持评估，
  但网页与 API 尚未暴露“角色找角色”的发送。
- **安静时段使用服务器本地时间**，没有用户时区字段。
- **未回复只按“用户是否发言”清零**：用户发一句与主动消息无关的话也会清零；
  当前会话没有消息 ID / 引用关系，无法做逐条已读。
- **单线程假设不变**：调度器本身无锁，由 `/scene` 的 `_scene_lock` 串行化。

下一步优先级建议：每角色独立 appraisal → 调度状态持久化 / 外部定时器 →
LLM 主动台词与逐条已读。
