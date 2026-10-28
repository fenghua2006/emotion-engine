# 架构与维护边界

> 目标：让每个 L 阶段都能独立修改、独立测试，不靠“读完整条 tick”才能判断影响。
> 适用版本：v0.8.5-dev + P0 可维护性重构 + L6（JEV / Noul / MCP）+ L7（多角色情绪感染 + 网页实验台）+ L8（朋友圈场景）+ L8.5（主动联系）。
> 相关：`docs/CORE.md`（L0）、`docs/INCREMENTAL_PLAN.md`（阶段计划）。

## 1. 依赖方向

```text
core.py              纯动力学；只依赖标准库
  ├── atmosphere.py  L3 纯变换；只依赖 core.Channel
  ├── tolerance.py   L5 控制层；只依赖标准库
  ├── meta_emotion.py L3.5 只读观测；只依赖 core + 标准库
  ├── memory.py      L4 关系域记忆；SQLite，不导入 engine
  ├── expression.py  表达层；只读 state / tolerance，不导入 engine
  ├── llm_client.py  网络薄客户端；不知道情绪状态
  ├── jev.py         L6 文本 → Appraisal；只依赖 core + llm_client
  ├── noul.py        L6 文本边界信号；只依赖标准库
  ├── contagion.py   L7 多角色感染；只依赖 core.APPRAISAL_LIMITS + 标准库
  ├── proactive.py   L8.5 主动联系调度；只读状态，不导入 engine / social
  ├── social.py      L8 朋友圈编排；依赖 contagion / proactive + 标准库，不导入 engine
  └── engine.py      组合根：按顺序调用以上模块
        ├── mcp_server.py  零依赖 stdio 外壳；只调用 engine / memory / jev / noul
        ├── dashboard/     单角色面板 + L7 多角色实验台 + L8 朋友圈 + L8.5 主动联系；只读 engine 输出，不反向写动力学
        └── characters/    只构造 Personality / Engine
```

硬规则：

- 下层不反向导入 `engine.py`。
- 表达层不参与动力学；改台词 / prompt / Live2D 映射不应改变 `tick()` 数值。
- LLM 客户端不读 `EmotionEngine`；只接收 prompt，失败返回 `(None, None)`。
- 记忆可以影响 appraisal / relationship / prompt，但**永不写** `Personality` / `CharacterCore`。
- JEV 只返回 `Appraisal` / confidence / evidence，不写 state、人格或记忆；Noul 只返回文本信号。
- L7 感染只返回四维有界 `Appraisal` 偏置；不直接写 `EmotionalState`，不写记忆 / 人格 / 快照。
- L8 只在引擎外部编排：持久化有向关系、调用 L7 偏置、回合后更新关系；不新增 `tick()` 阶段，不写引擎状态。
- L8.5 只读评估主动联系意愿；`mark_contact()` 只更新调度计数，不 tick、不写状态 / 记忆 / 人格 / 关系；发送必须复用普通场景回合。
- `mcp_server.py` 不导入 dashboard，也不修改 engine 的内部状态结构；工具失败以 `isError` 返回。

## 2. tick 管线

`EmotionEngine.tick()` 现在只做编排，实际计算按固定顺序拆到 `_step_*`：

| 顺序 | 方法 | 作用 |
|---:|---|---|
| 0 | `_begin_tick()` | 时钟、NaN/Inf 防护、氛围入口 clamp、trace before、保存上一帧 |
| 1 | `_step_decay()` | 思念消退、快通道弹性衰减、基线回弹 |
| 2 | `_step_slow_channels()` | love / trust 生长；正面事件按本 tick 增量输入 |
| 3 | `_step_appraisal()` | L4 记忆先验 → L7 感染先验 → appraise → gate → 耐受增益 → 伤疤 → 氛围 → 写入 state |
| 4 | `_step_after_appraisal()` | 唤醒度采样、legacy 晚调制、越界惩罚、trace appraisal 字段 |
| 5 | `_step_interactions()` | 交互矩阵；负性强度受耐受窗调制 |
| 6 | `_step_shock()` | 对比度冲击检测；corrected 模式更新氛围 |
| 7 | `_step_memory_and_saga()` | 旧版记忆存储 / 固化 / Saga 拉力 |
| 8 | `_step_meta_and_tolerance()` | 可选 meta 负荷 → 刷新耐受窗 → trace final |
| 9 | `_build_tick_result()` | 组装 state / felt / blends / trace 等返回值 |

**顺序即契约。** `test_p0_maintainability.py` 会记录各方法调用顺序；改动顺序必须同时重跑：

```bash
python -m unittest discover
python scripts/generate_l1_golden.py --check
python scripts/generate_l1_golden.py --legacy --check
python scripts/generate_l2_golden.py --check
python scripts/generate_l2_golden.py --legacy --check
python scripts/generate_l5_tolerance_golden.py --check
python scripts/generate_l5_tolerance_golden.py --legacy --check
```

`_TickContext` 只是 tick 内的可变上下文，不是公开 API；新增阶段字段可以加在它上面，但不要把它写进快照或对外返回值。

## 3. 表达层与 LLM

`expression.py` 包含：

- `_action_tendency()`：趋近 / 回避 / 僵住
- `_CHARACTER_POOLS` / `_ZONE_POOLS`：本地台词池
- `_select_local_utterance()`：按 zone 优先选台词
- `_emotion_snapshot()` / `_dominant_channel()`
- `_mood_hint()`：情绪数值 → LLM 口吻指令
- `_build_llm_prompt()`：prompt 组装
- `_expression_params()`：Live2D 参数映射

`engine.py` 仍 re-export 这些名字，旧 `from engine import _build_llm_prompt` 不会断。

`llm_client.call_llm()` 只负责：

1. 按主 API → fallback API 顺序尝试；
2. 返回 `(文本, backend 标签)`；
3. 全部失败时返回 `(None, None)`，由 `respond_llm()` 回退到本地模板。

`respond_llm()` 仍保留记忆召回、prompt 构建、turn 写入等编排职责。网络调用不要重新塞回 `engine.py`。

### L6 文本入口

`jev.py` / `noul.py` 是可选输入端，不进入 `tick()` 内部：

- `respond_llm(..., use_jev=True)` 先得到 `Appraisal`，再走原来的 `tick()`；
  规则优先，低置信度时才可能调用 LLM（`use_llm=True`）。
- `auto_boundary=True` 用 `noul.detect()` 生成建议；显式 `boundary_violation`
  优先。两者都失败时只记录 `_jev_error` / `_boundary_error`，不中断主链路。
- 所有开关默认关闭；默认路径的返回值键集合与旧版一致。
- `mcp_server.py` 只负责协议与编排：`tick` 复用 `respond` / `respond_llm`，
  `recall` 复用 `memory.store.recall` + `render_context`，`state` 复用
  `engine.memory.stats()`。它不实现第二套情绪逻辑。
- `scripts/replay_trace.py` 与 dashboard `POST /api/replay` 用副本回放，
  不写快照 / 记忆。

### L7 多角色感染

`contagion.py` 是纯组合层，不导入 `engine.py`，通过 duck typing 调用 `.tick()`：

- `derive_contagion_bias(source_appraisal)` 只取四维
  （`goal_conduciveness` / `expectedness` / `coping_potential` /
  `social_evaluation`），按中性值归一化后乘 `gain`，单维截断到 `±cap`。
- `sanitize_contagion_bias()` 是引擎边界：只接受 Mapping，拒绝 bool /
  非数值 / NaN / Inf，逐维 clamp，空结果返回 `None`。
- `engine.tick(contagion_bias=...)` 在 L4 记忆先验之后、`appraise()` 之前
  叠加；没有 `Appraisal` 时完全 no-op。
- `ContagionPair` 负责隔离：拒绝同一引擎或共享 `state` / `memory` /
  `scars` / `tolerance`；共享只读 `Personality` 允许。
- `strength` 是关系强度乘数：`effective_gain = gain * strength`，
  限制在 `[0, 1]`；`strength=0` 等价于关闭感染。
- `relationship_strength()` / `auto_strength=True`：从双方 `state.trust` /
  `state.love` 推导强度；自动模式在每次 `derive()` 前刷新，仍受 `cap` 限制。
- `tick_bidirectional()` 从双方本轮的输入 appraisal 先算出两个方向的偏置，
  再 tick A、B；没有同 tick 反馈环，结果与 tick 顺序无关。
- `ContagionNetwork` / `ContagionEdge`：有向多跳传播；第一跳用边强度，
  之后每跳乘 `hop_decay`（默认 0.5），最多 `max_hops`（默认 3，上限 8）；
  同源同目标保留最强路径，多源求和后仍逐维截断到 `±cap`。所有节点先算
  偏置再 tick，因此传播链没有同 tick 反馈。
- `DirectionalRelationship` / `directional_relationship_strength()`：
  分别计算 A→B 与 B→A，由 trust / love / familiarity 归一化加权，边界
  条目按数量乘 `1 / (1 + penalty × count)`；`directional` 与
  `auto_strength` 互斥。
- 默认路径不新增 result / trace 键；只有感染实际运行时才追加
  `result["contagion_bias"]` 与三个 trace 字段。
- 感染是每 tick 输入，不写入快照或记忆；`SNAPSHOT_VERSION` /
  `MEMORY_SCHEMA_VERSION` 不变。慢通道在 appraisal 前运行，因此同一 tick
  的 love / trust 生长仍看原始 appraisal。
- `dashboard/contagion.html` + `/api/contagion/*` 是实验台外壳：每个节点
  新建独立引擎，配置签名变化时重建；tick 状态只在进程内保留，不读写单角色
  仪表盘存档。它只展示 L7 输出，不实现第二套感染逻辑。
- 详细契约与演示：[`L7_MULTI_CHARACTER.md`](L7_MULTI_CHARACTER.md)。

### L8 朋友圈场景

`social.py` 是引擎外部的场景编排层，不导入 `engine.py`，通过 duck typing
调用 `.tick()`；它依赖 `contagion.py` 的纯函数与边模型：

- `PeerRelationship`：有向关系快照，键为
  `(world_id, source_id, target_id)`；字段 trust / love / familiarity /
  tension / boundaries / summary / interactions。
- `PeerRelationshipStore`：标准库 SQLite，schema
  `SOCIAL_SCHEMA_VERSION = 1`；world 级隔离，默认 `:memory:`，仪表盘默认
  `dashboard/_social_relationships.db`。
- `FriendScene.edges()`：边强度 =
  `directional_relationship_strength(...) × (1 - tension_penalty × tension)`；
  tension 越高，感染越弱。
- `FriendScene.biases_for()`：只允许角色发言者，返回 L7 四维有界偏置；
  可选 `relevance` 逐目标门控。
- `FriendScene.record_turn()`：按听者的 appraisal 更新“听者 → 发言者”；
  用户发言只推进场景回合，不改角色间关系。
- `FriendScene.step()` 是完整库路径；仪表盘改用
  `biases_for()` + `respond()` + `record_turn()`，避免每个角色被 tick 两次。
- 场景引擎的情绪状态不持久化，只持久化关系；不改变默认 tick，不新增
  result / trace 键，不新增 golden。
- `dashboard/scene.html` + `/api/scene/*` 是网页外壳；单线程 `HTTPServer`
  下 SQLite 单连接可用，改多线程时需按线程建连接或加锁。
- 详细契约、API 与限制：[`L8_SOCIAL_SCENE.md`](L8_SOCIAL_SCENE.md)。

### L8.5 主动联系

`proactive.py` 是引擎外的确定性调度层，只读 `actor.engine.state` 与关系快照：

- `ProactiveConfig`：静默 / 冷却 / 每日 / 未回复 / 安静时段 / 阈值 / 权重；
- `ProactiveScheduler`：`assess / evaluate / eligible / choose` 纯读；
  `observe_turn` 记录活动，用户发言清空 `pending`；
  `mark_contact` 仅更新调度状态；
- urgency 由思念、静默、亲密、信任、熟悉、紧张、未回复线性组合；
- 门控顺序固定：安静时段 → 最小静默 → 冷却 → 每日上限 → 未回复上限 → 阈值；
- `FriendScene` 持有调度器，`record_turn()` 自动观察，`snapshot()` 增加
  `proactive`；调度状态不持久化，关系仍持久化；
- 发送由 `/api/scene/proactive` 触发：生成模板台词后走一次普通
  `_scene_turn`，最后 `mark_contact`，因此不重复 tick；
- 不新增 `tick()` 阶段、result / trace 键或 golden；
- 详细契约、API 与限制：[`L8_5_PROACTIVE_CONTACT.md`](L8_5_PROACTIVE_CONTACT.md)。

## 4. 版本与迁移

### 快照 payload

`engine.SNAPSHOT_VERSION` 当前为 `2`。

- v1：无 `schema_version` 的旧 payload，或 v0.5–v0.6 直接把通道字典放在 `state_json`。
- v2：当前完整 payload，包含 `schema_version`、state、tolerance、legacy flags 等。

`_migrate_snapshot_payload()` 统一旧格式；来自更新版本的快照返回 `None`，`load_snapshot()` 返回 `False`，不会静默截断。

增加字段时：

1. 递增 `SNAPSHOT_VERSION`；
2. 在 `_migrate_snapshot_payload()` 里按 `version` 补迁移；
3. 在 `test_p0_maintainability.py` 加旧版本 / 未来版本测试。

### 记忆 schema

`memory.MEMORY_SCHEMA_VERSION` 当前为 `1`，写入 SQLite `PRAGMA user_version`。

- `0`：L4 之前的旧库；`CREATE TABLE IF NOT EXISTS` 补齐基线。
- `1`：当前基线 schema。

迁移入口是 `RelationshipMemoryStore._migrate_schema(from_version)`。升级表结构时递增版本号，并在该方法里追加 `if from_version < N` 分支；来自未来版本的库会抛 `RuntimeError`，避免旧代码破坏新数据。

## 5. 工程护栏

仓库根目录：

- `pyproject.toml`：ruff + coverage 配置；ruff 只门禁 `E` / `F`（忽略 `E501`），避免历史风格噪音。
- `.github/workflows/ci.yml`：lint → 全量 unittest → coverage gate（当前 `fail_under=80`）→ 六套 golden。
- `test_p0_maintainability.py`：表达层 re-export、tick 顺序、快照版本、记忆 schema 版本、LLM 无 key / fallback。

本地命令：

```bash
python -m pip install ruff coverage
ruff check .
coverage run -m unittest discover
coverage report
```

新增核心模块时，把它加入 `[tool.coverage.run].source`；新增测试文件不需要改配置。

## 6. 修改某个 L 阶段时

1. 先确认改动属于哪一层：纯公式（`core.py`）、控制层（`tolerance.py`）、观测层（`meta_emotion.py`）、记忆（`memory.py`）、表达（`expression.py`）、文本入口（`jev.py` / `noul.py`）、多角色感染（`contagion.py`）、社交场景（`social.py`）、协议外壳（`mcp_server.py`）、编排（`engine.py`）。
2. 如果改变默认曲线，增加 legacy 开关和前后对照测试；保留旧 golden。
3. 如果新增返回字段，默认路径的键集合保持不变；需要时用独立开关控制。
4. 如果新增持久化字段，同时更新快照 / 记忆 schema 版本与迁移测试。
5. 跑全量测试 + 六套 golden；不要只跑新增测试。
6. 更新本文件、对应 L 文档和 `docs/INCREMENTAL_PLAN.md` 的状态。

## 7. 已知边界

- `engine.py` 仍是组合根，包含旧版内存 `MemoryStoreDB`、快照 SQLite 与 demo；后续可以继续拆，但不要为了“行数少”改变行为。单角色仪表盘已不再使用 `MemoryStoreDB`，持久记忆改走 `RelationshipMemoryStore`。
- `dashboard/server.py` 仍读取 engine 的私有 `_dominant_channel`；它通过 re-export 工作，后续可改为公开接口。
- `dashboard/server.py` 的 relationship store 与 `PeerRelationshipStore` 都是单连接；当前 `HTTPServer` 单线程处理请求，因此连接创建与使用在同一线程。若改成多线程服务器，需要给存储加锁或按线程建连接。
- 测试中仍有直接访问私有成员的断言；这是为了锁定分阶段行为，不是对外 API 承诺。
- 覆盖率 gate 只覆盖核心模块与 L6 的 `jev` / `noul` / `mcp_server`、L7 的 `contagion`、L8 的 `social`、L8.5 的 `proactive`；`dashboard` / `scripts` / `characters` 不做覆盖率门禁。
- JEV v0 是子串 / 词边界规则，不处理反讽、长距离指代与复杂否定；LLM 补充只做 JSON 夹取，不做多轮自我校验。
