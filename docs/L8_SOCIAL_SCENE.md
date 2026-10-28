# L8：朋友圈场景（v0）

> 代码：`social.py` · 测试：`test_l8_scene.py` + `test_scene_dashboard.py` · 网页：`dashboard/scene.html`（`/scene`）
> 前置：L7 多角色情绪感染（`contagion.py`）

L8 v0 把“多角色感染”从一个实验台推进到**可连续对话的朋友圈场景**：

- 每个角色拥有完全隔离的引擎、记忆、伤疤与耐受；
- 角色之间维护**有向关系**（A 对 B 的关系不等于 B 对 A）；
- 角色发言时，其他角色收到基于关系强度的四维感染偏置；
- 回合结束后，听者按自己对这句话的 appraisal 更新“听者 → 发言者”的关系；
- 关系可持久化到 SQLite，并按 `world_id` 隔离不同群聊 / 剧本。

L8 v0 **不做**主动联系、日程调度、消息推送或关系历史文本生成。这些属于
后续的 L8.5 / 更高层编排，不应塞进 `tick()`。

---

## 1. 分层与硬边界

```text
engine.py（单角色动力学）
  └── contagion.py（L7 纯函数：appraisal → 有界四维偏置）
        └── social.py（L8 编排：关系快照 + 场景回合）
              └── dashboard/server.py + scene.html（/scene 外壳）
```

`social.py`：

- 不导入 `engine.py`，通过 duck typing 调用 `engine.tick()`；
- 不修改 `EmotionalState` 公式、不写 `Personality` / `CharacterCore`；
- 不共享 `state` / `memory` / `scars` / `tolerance`；
- 不改变单角色默认路径，因此 L1 / L2 / L5 六套 golden 逐位不变；
- 只依赖标准库 + `contagion.py`。

`FriendScene` 只负责“谁在什么时候对谁产生了什么偏置、关系如何结算”，
不实现第二套情绪动力学。

---

## 2. 数据模型

### 2.1 `PeerRelationship`

有向关系，主键为 `(world_id, source_id, target_id)`：

| 字段 | 范围 | 含义 |
|---|---|---|
| `trust` | `[0, 1]` | 听者对发言者的信任 |
| `love` | `[0, 2]` | 亲密 / 爱意 |
| `familiarity` | `[0, 1]` | 熟悉度，随互动缓慢累积 |
| `tension` | `[0, 1]` | 关系紧张度，削弱感染边 |
| `boundaries` | 最多 20 条 | 听者认定的边界条目 |
| `summary` | 文本 | 最近一次结算的简短摘要 |
| `interactions` | 非负整数 | 关系结算次数 |
| `updated_at` | Unix 时间 | 最近更新时间 |

默认值：`trust=0.2`、`love=0.2`、`familiarity=0`、`tension=0`。
所有数值在构造和写入时 clamp；`source_id == target_id` 会直接拒绝。

### 2.2 `PeerRelationshipStore`

SQLite 存储，表 `peer_relationships`，schema 版本 `SOCIAL_SCHEMA_VERSION = 1`。
`world_id` 是作用域：同一个 A、B 在 `friends` 与 `office` 两个世界中互不影响。

```python
from social import PeerRelationshipStore

store = PeerRelationshipStore("dashboard/_social_relationships.db")
# 不传路径时使用 :memory:，方便测试
```

### 2.3 `SceneActor` / `FriendScene`

```python
from social import FriendScene, SceneActor

scene = FriendScene(
    [
        SceneActor("A", "芙宁娜", engine_a, "furina"),
        SceneActor("B", "心海", engine_b, "kokomi"),
    ],
    world_id="friends",
    store=store,
    gain=0.2,
    cap=0.08,
    hop_decay=0.5,
    max_hops=2,
)
```

构造时检查：

- actor ID 不重复；
- 引擎不重复；
- `state` / `memory` / `scars` / `tolerance` 不共享。

`FriendScene` 的 `turn` 是场景回合计数，不是任何单个引擎的 tick 计数。

---

## 3. 一个场景回合

### 3.1 计算感染偏置

```python
biases = scene.biases_for("A", appraisal_a)
# {"B": {"goal_conduciveness": -0.03, ...}, ...}
```

- 只有**角色发言者**会产生偏置；`biases_for("user", ...)` 返回 `{}`。
- 偏置通过 L7 的 `ContagionNetwork.derive()` 计算，仍然是四维、`gain` 缩放、
  单维 `±cap` 截断。
- 边权来自当前关系：

```text
base = directional_relationship_strength(trust, love, familiarity, boundaries)
strength = base × (1 - tension_penalty × tension)
tension_penalty 默认 0.25
```

关系越好，感染越强；关系越紧张，感染越弱。

- `relevance={"B": 0.4, "C": 0.0}` 是可选目标门控（0–1）。`0` 表示这件事
  与 C 无关，C 不接收偏置。v0 仪表盘未暴露该参数。

### 3.2 tick 每个角色

仪表盘使用：

```python
biases = scene.biases_for(speaker_id, appraisal)
for actor_id, actor in scene.actors.items():
    respond(actor.engine, text, appraisal=appraisal,
            contagion_bias=biases.get(actor_id))
```

这样每个角色**只 tick 一次**，不会出现 `FriendScene.step()` 与 `respond()`
重复 tick 的问题。

### 3.3 结算关系

```python
turn = scene.record_turn(
    "A",
    {"A": appraisal_a, "B": appraisal_b, "C": appraisal_c},
    boundary_violations={"B": False, "C": False},
)
```

对每个听者 T（T ≠ 发言者 S），更新 **T → S**：

```text
valence = 0.5 × goal_conduciveness + 0.5 × social_evaluation
trust_delta       = 0.02 × valence
love_delta        = 0.005 × max(0, valence)
familiarity_delta = 0.004 + 0.006 × goal_relevance
tension_delta     = 0.04 × max(0, -valence)

若 boundary_violation：
    trust_delta -= 0.05
    tension_delta += 0.08
```

随后 clamp、`interactions += 1`、生成简短 `summary` 并写回 store。

口径：

- 关系更新用的是**听者自己的 appraisal**；同一个发言者对不同听者可以产生
  不同关系变化。
- `record_turn("user", ...)` 仍然推进场景回合，但**不更新任何角色间关系**。
- 关系更新不写引擎的 `state` / `memory` / `scars` / `tolerance`。
- `step()` 是库内测试 / 离线编排的完整版本：算偏置 → tick 所有角色 →
  结算关系。仪表盘为了生成台词，使用 `biases_for()` + `respond()` +
  `record_turn()` 的组合。

---

## 4. 持久化

- 默认（库内）使用 `:memory:`；仪表盘默认
  `dashboard/_social_relationships.db`，可用 `--social-db` 覆盖。
- 只持久化**关系**，不持久化场景引擎的情绪状态、记忆或伤疤。
  重启后角色情绪回到构造时状态，但关系保留。
- `_scene_reset` 默认保留当前 `world_id` 的关系；勾选
  “清空本世界的角色关系”或传入 `reset_relationships=True` 才会删除。
- 换 `world_id` 等于换一个朋友圈，不会读到旧关系。

---

## 5. 网页实验台 `/scene`

启动：

```bash
python dashboard/server.py
# http://localhost:9020/scene
```

页面能力：

- 2–4 个角色，每个角色独立选择蒸馏角色；
- 编辑 `world_id`、`gain`、`cap`、`hop_decay`、`max_hops`；
- 选择发言者（用户或任一角色），输入文本；
- 每回合展示每个角色的台词、dominant、zone、felt 情绪条与感染偏置；
- 有向关系表：trust / love / familiarity / tension / 互动次数 / 边界；
- 感染边强度与每回合关系结算增量；
- 应用配置时可选清空当前世界的关系。

API：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/scene/state` | 当前场景快照、关系、边、最近 40 回合日志 |
| `POST` | `/api/scene/reset` | `{"config":{...},"reset_relationships":false}` 重建场景 |
| `POST` | `/api/scene/turn` | `{"speaker_id":"user","text":"..."}` 或显式 `appraisal` |

`/api/scene/turn` 的文本先经过 JEV 解析成六维 `Appraisal`；也可以直接传
`appraisal` 字典（逐维 clamp 到合法范围）。响应包含：

```text
turn, source, text, appraisal, biases,
actors{utterance, dominant, felt, zone, contagion_bias, ...},
relationship_changes, scene{...}, config
```

`turn_log` 只保留最近 40 回合，存于进程内；关系本身在 SQLite 中。

---

## 6. 测试与验收

```bash
python -m unittest test_l8_scene.py test_scene_dashboard.py -v
```

- `test_l8_scene.py`（15）：关系存取与世界隔离、clamp / 自环、共享引擎拒绝、
  方向边与 tension、源专属偏置、relevance 门控、正负关系更新、边界惩罚、
  持久化重开、`step()` 隔离、快照结构、用户回合不更新关系、人格不变。
- `test_scene_dashboard.py`（13）：场景 payload、回合偏置与台词、用户回合、
  方向关系、负面 tension、重建保留 / 清空关系、配置变更清日志、引擎隔离、
  日志上限、非法输入、appraisal clamp、store 重开、页面接口冒烟。

当前结果：

```text
513 tests total, OK（含 L8.5 的 30 个测试）
ruff check . -> All checks passed
coverage report -> 88% total（branch，gate 80）
L1/L2/L5 六套 golden 无 diff
```

L8 没有新增 golden：它不改变任何默认 tick 公式，只编排隔离引擎；契约由
28 个 L8 测试、默认路径键集合不变、六套既有 golden 不变共同锁定。

---

## 7. 已知限制

- **主动联系在 L8.5 独立实现**：L8 v0 只处理“有人说话之后”的反应。
  主动发起、冷却、安静时段、每日上限与未回复计数在 `proactive.py` 中完成，
  不进入 `tick()`；见 [`L8_5_PROACTIVE_CONTACT.md`](L8_5_PROACTIVE_CONTACT.md)。
- **关系更新公式是 v0 手调常数**，没有学习；`summary` 只是数值摘要，
  不是 LLM 生成的关系历史。
- **appraisal 目前由发言文本统一生成**，同一回合所有角色使用同一份
  appraisal；后续可接入每个角色自己的 JEV / 记忆上下文。
- **没有群体级归一化**：每个听者独立截断偏置。
- **没有关系历史表**：只保留当前关系快照与最近 40 回合进程内日志。
- **场景引擎状态不持久化**：重启后情绪归零，关系保留。
- 仪表盘仍是单线程 `HTTPServer`；`PeerRelationshipStore` 的单连接假设与
  现有 `RelationshipMemoryStore` 相同。改成多线程服务器时需要按线程建连接
  或加锁。

---

## 8. 后续

- **L8.5 主动联系**：`proactive.py` 提供引擎外的意愿评估与调度，
  `FriendScene.record_turn()` 自动观察，`/api/scene/proactive` 支持
  check / send；发送复用普通场景回合，不重复 tick。详细契约见
  [`L8_5_PROACTIVE_CONTACT.md`](L8_5_PROACTIVE_CONTACT.md)。
- 更后期：每角色独立 JEV / appraisal、关系历史与学习型更新、群体级归一化。
