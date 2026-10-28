# Emotion Engine

多通道情绪交互引擎。10 个独立情绪通道，无硬上限，弹性衰减，论文验证——为 AI 伴侣/NPC/角色扮演提供可观测的情绪动力学。

## 为什么不用 PAD

PAD（Pleasure-Arousal-Dominance, 1974）把情绪压缩成三维坐标中的单点。真实情绪不是这样的——

> 你可以同时感到愤怒和悲伤。它们不会互相抵消。它们互相调制。

Emotion Engine 给每种情绪一个独立通道。joy=0.6 + sadness=0.5 同时存在，这是"苦涩的微笑"，不是一个坐标点。50 年后，该有人替代它了。

## v0.8.5 架构

```
                    ┌──────────────┐
                    │   Atmosphere  │  -1 relaxed ~ +1 tense
                    │   氛围修饰器   │  写入前调制 gated appraisal
                    └──────┬───────┘
                           │
    ┌──────────────────────┼──────────────────────┐
    │                      │                      │
    ▼                      ▼                      ▼
┌────────┐          ┌────────────┐         ┌──────────────┐
│  Fast  │◄─────────│   Slow     │         │   Absence    │
│  6 ch  │  gate    │  trust     │  couple │  longing     │
│ joy    │◄─────────│  love      │────────►│  guilt       │
│ sad    │   modulate│           │         │              │
│ anger  │          └────────────┘         └──────────────┘
│ fear   │
│ disgust│          ┌────────────┐
│surprise│          │ Interaction│  情绪间不抵消，互相调制
└───┬────┘          │   Matrix   │
    │               └────────────┘
    ▼
┌─────────┐    ┌──────────┐    ┌──────────────┐
│ Elastic │───►│  Memory  │───►│ Sensitization │
│ Decay   │    │  Arousal │    │ Kindling +    │
│无硬上限  │    │  固化窗口 │    │ Bayesian 威胁  │
└─────────┘    └──────────┘    └──────────────┘
```

`tolerance.py` 不新增情绪通道，而是在每次 tick 后读取状态，决定下一 tick 是调节态、过度唤醒还是低唤醒。L5 默认把容量拆成愤怒 / 恐惧 / 亲密 / 批评四域，再取加权有效容量。

底层公式已拆到 `core.py`：通道定义、弹性衰减、六维评估、门控、交互矩阵、慢通道生长。`engine.py` 只负责组合记忆 / 耐受 / 氛围 / 伤疤 / 表达。L6 在此之上增加文本入口（`jev.py` / `noul.py`）与 MCP 外壳（`mcp_server.py`）；L7 增加多角色情绪感染（`contagion.py`，单向 + 双向 + 显式 / 自动关系强度 + 群体传播 + 方向性关系）；L8 增加朋友圈场景（`social.py`，有向关系持久化 + 场景回合编排）与可选 LLM 台词（默认关闭，失败回退本地模板）；L8.5 增加主动联系调度（`proactive.py`，只读意愿评估 + 独立冷却 / 上限）。这些入口默认关闭，不改变默认曲线。

## 快速开始

```bash
git clone https://github.com/fenghua2006/emotion-engine
cd emotion-engine
uv run python engine.py
uv run python characters/kokomi.py
```

### 实时仪表盘

```bash
# 启动仪表盘（默认角色）
uv run python dashboard/server.py

# 指定角色
uv run python dashboard/server.py --char furina
uv run python dashboard/server.py --char kokomi
uv run python dashboard/server.py --char columbina

# 持久记忆：所有角色共享一个关系域库，但按 (character_id, user_id) 隔离
uv run python dashboard/server.py --memory-db dashboard/_relationship_memory.db --user-id alice

# 浏览器打开 http://localhost:9020
# 网页内可热切换角色、发送事件、查看情绪折线历史
# 耐受窗卡片展示 load / capacity / 四域容量，并按 zone 绘制区间带
# 单角色仪表盘的持久记忆已统一到 RelationshipMemoryStore；
# 未保存的 custom 角色按名字拆成 custom:<name>，不会共用同一记忆域。

# L7 多角色感染实验台
# http://localhost:9020/contagion
# 预设：群体链 A→B→C / 双向 A↔B / 方向关系 / 全部中性
# 可编辑节点关系、边权、gain / cap / hop_decay / max_hops，
# 逐轮 tick 并查看每节点感染偏置、情绪条与感染日志。

# L8 朋友圈场景
# http://localhost:9020/scene
# 2–4 个角色连续对话；关系按 (world_id, source, target) 持久化到 SQLite，
# 感染边由关系强度推导，tension 会削弱传播。
uv run python dashboard/server.py --social-db dashboard/_social_relationships.db
```

API 端点（供外部接入）：
| 端点 | 说明 |
|---|---|
| `GET /api/state` | 当前 10 通道 felt 值 + 氛围 + tolerance + dominant + 记忆 + 伤疤；含 `memory_scope` 与关系域计数 |
| `GET /api/history?n=120` | 最近 N 个数据点（情绪与 tolerance 折线用；含 zone / 四域容量） |
| `POST /api/event` `{"text":"..."}` | 发送事件，引擎 tick |
| `POST /api/switch` `{"character":"kokomi"}` | 热切换角色 |
| `GET /api/contagion/state` | 当前多角色实验台状态 |
| `POST /api/contagion/reset` `{"config":{...}}` | 按配置重建隔离引擎 |
| `POST /api/contagion/tick` `{"config":{...},"appraisals":{...},"steps":1}` | 执行 1–20 轮感染 |
| `POST /api/contagion/appraise` `{"text":"..."}` | 用 JEV 把文本解析成六维 Appraisal |
| `GET /api/scene/state` | 当前朋友圈场景、有向关系、感染边与最近回合 |
| `POST /api/scene/reset` `{"config":{...},"reset_relationships":false}` | 重建场景；可选清空当前世界的关系 |
| `POST /api/scene/turn` `{"speaker_id":"user","text":"..."}` | 跑一个场景回合；角色发言时按关系传播偏置 |
| `POST /api/scene/proactive` `{"action":"check"}` / `{"action":"send","actor_id":"B"}` | 检查 / 发送主动联系；发送会走一次普通场景回合 |

## 基本用法

```python
from engine import (
    EmotionEngine, EmotionalState, Personality, Appraisal,
    MemoryStore, SensitizationStore
)

pers = Personality(extraversion=0.7, neuroticism=0.6, agreeableness=0.7)
state = EmotionalState()
engine = EmotionEngine(
    state=state, personality=pers,
    memory=MemoryStore(), scars=SensitizationStore()
)

result = engine.tick(Appraisal(
    goal_relevance=0.8, goal_conduciveness=-0.7,
    expectedness=0.2, other_agency=0.9,
    coping_potential=0.2, social_evaluation=-0.3
))
# result["state"] → 10 通道原始值
# result["felt"]  → log 压缩感知值
# result["memory"] → 记忆统计
# result["atmosphere"] → 氛围值
# result["tolerance"] → 负荷 / 有效容量 / 四域容量 / zone / 滞回状态
```

## L0 核心情绪线

```python
result = engine.tick(appraisal, now=now, trace=True)
trace = result["trace"]
# corrected: before → after_decay → after_slow_channels → after_appraisal
#            → after_interaction → after_atmosphere → final
# legacy:    ... → after_appraisal → after_atmosphere
#            → after_interaction → final
```

- `core.py` 不导入 `engine` / `atmosphere` / `tolerance` / `memory`，可以单独测试底层曲线。
- `tick(trace=False)` 与原行为完全一致；`trace=True` 只增加观察快照。
- 28 个核心测试锁住衰减、门控、慢通道、评估与 trace 行为；场景 LLM 表达后全量 523 tests。
- 契约、分层规则与顺序问题（L3 已修）：[`docs/CORE.md`](docs/CORE.md)。
- P0 可维护性：`tick()` 拆成有序 `_step_*`、表达层 / LLM 客户端分层、ruff + coverage + CI、快照 / 记忆 schema 版本与迁移点；见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
- L0–L8 + L3.5 开发安排与验收：[`docs/INCREMENTAL_PLAN.md`](docs/INCREMENTAL_PLAN.md)。
- 测试：`python -m unittest test_core_line.py -v`。

## L1 六条快通道（已完成）

`fastline.py` 把 joy / sadness / anger / fear / disgust / surprise 锁成一条固定参考线：固定 OCEAN=0.5、固定 `trust=love=0.5`、固定 10 分钟步长，不接 tolerance / 伤疤 / 氛围 / 记忆。

- 46 条冻结事件：角色 31 + engine demo 3 + 合成边界 12。
- 双 golden：`fixtures/l1_golden.json`（当前）+ `l1_golden_legacy.json`（v0.7 对照），误差 < `1e-9`。
- 22 个 L1 测试 + 11 个交互语义测试；L2 完成后全量 134 tests，L3 完成后 166 tests，
  L3.5 完成后 191 tests，L5 校准核心完成后 229 tests，L5.1 表达/仪表盘后 241 tests，
  L4 记忆接入后 285 tests，L6 JEV / Noul / MCP 后 338 tests，
  L7 情绪感染与网页实验台后 455 tests，L8 朋友圈场景后 483 tests，
  L8.5 主动联系后 513 tests，可选场景 LLM 表达后 **523 tests, OK**。
- L1.1 修复：交互改为基线相对激活 + 快通道目标共激活；空闲 300 tick 六通道回到基线，纯负面事件不再凭空加 joy。旧行为由 `legacy_interactions=True` 和 legacy golden 保留。
- 剩余已知问题：`dominant` 沿用“最高原始值 > 0.3”，joy 基线 0.35 造成基线偏差。
- 生成 / 校验：`python scripts/generate_l1_golden.py [--check] [--legacy]`。
- 详细记录：[`docs/L1_FAST_CHANNELS.md`](docs/L1_FAST_CHANNELS.md)。
- 测试：`python -m unittest test_l1_fast_channels.py test_interaction_activation.py -v`。

## L2 慢通道与缺失通道（已完成）

`slowline.py` 把 trust / love / longing / guilt 锁成一条可复现的十通道参考线：
在线走 `tick()` 的慢通道路径，离线走 `wake()` 的压缩衰减与思念生长路径。

- 19 条冻结事件，覆盖关系正负事件、guilt 门槛、越界、离线 2h / 24h / 8d / 30d。
- 双 golden：`fixtures/l2_golden.json`（当前）+ `l2_golden_legacy.json`（旧慢通道 + 绝对阈值交互）。
- 32 个 L2 测试；L2 完成时全量 134 tests；四套 golden 误差 < `1e-9`。
- **交互基线**：trust 用 `trust_target(love)`，love / 六快通道 / guilt 用人格基线。原因是正常关系里的 trust 会长到目标；若把人格局限当成“额外信任”，空闲 tick 会持续压 fear（实测 300 tick 将 fear 从 0.105 拉到 0.0）。
- **guilt 门槛**：`self_agency ≤ 0.3` 严格为 0，超过后平滑放大；纯外部归因不产生愧疚。
- **正面事件**：love 只吃本区间新增事件，不再用累计值；30 天模拟 trust 收敛到 `trust_target(love)`，不撞 1.0 上限。
- **数值护栏**：trust ∈ [0, 1]，love ∈ [0, 2]；`love → trust` / `guilt → trust` 的累计增量受关系均衡线封顶。
- legacy 开关：`legacy_slow_channels=True` + `legacy_interactions=True` 近似复现 L1.1 前行为。
- 生成 / 校验：`python scripts/generate_l2_golden.py [--check] [--legacy]`。
- 详细记录：[`docs/L2_SLOW_CHANNELS.md`](docs/L2_SLOW_CHANNELS.md)。
- 测试：`python -m unittest test_l2_slow_channels.py -v`。

## L3 氛围与伤疤（已完成）

L3 修正了 L0 留下的两处组合根顺序问题，并锁住伤疤的安全边界。默认 `corrected`；
`EmotionEngine(legacy_atmosphere=True)` 逐字保留 pre-L3 顺序和旧 trace notes。

corrected 顺序：

```text
appraise → gate → tolerance gain → scar amplification
→ atmosphere modulation → state write → boundary → interactions
→ shock detection → shock density → atmosphere drift（下一 tick 生效）
```

- **氛围前移**：tick t 的 atmosphere 在 state 写入前调制 joy / sadness / fear / anger；
  tick t 检测到的 shock 更新 tick t+1 的氛围。
- **shock 密度**：先检测 shock，再采样 `len(shock_channels) / len(FAST_CHANNELS)`；
  `_recent_shocks` 不再恒为 0。
- **有界**：corrected atmosphere ∈ `[-1, 1]`，单 tick 漂移 ≤0.1；`wake()` 和快照加载也 clamp。
- **伤疤**：只放大 sadness / fear / anger；effective shift ≤0.6，最大乘数 1.6；
  正面事件与 `goal_conducibility ≥ 0` 不放大。
- 32 个 L3 测试：氛围数学 7、伤疤数学 7、顺序 A/B 9、伤疤对照 3、快照 4、模块边界 2；
  L3 完成时全量 166 tests；L1/L2 四套 golden 逐位未变。
- 单次严重事件：corrected atmosphere `-0.200000`、shock density `0.714300`（5/7）；
  legacy 精确保留 `-0.3774803783593781`。
- 详细公式、A/B 数据与“为什么不设 L3 golden”：[`docs/L3_ATMOSPHERE_SCARS.md`](docs/L3_ATMOSPHERE_SCARS.md)。
- 测试：`python -m unittest test_l3_atmosphere_scars.py -v`。

## 元情绪（L3.5 v0，默认 trace-only；L5 可选消费耐受负荷）

元情绪不是第 11 个情绪通道，而是“以某个 primary emotion 为对象”的二阶过程：羞耻于愤怒、内疚于快乐、对恐惧感兴趣、为自己的平静骄傲。它读取最终状态与 `StepTrace`，输出表达、调节和耐受的有界偏置，不改写 `core.py` 公式，也不写 `Personality`。

```python
from engine import EmotionEngine, MetaEmotionProfile

engine = EmotionEngine(
    ...,
    meta_emotion_enabled=True,
    meta_emotion_profile=MetaEmotionProfile(
        name="shame_about_anger",
        kind_weights={"anger": {"shame": 2.0, "pride": 0.2}},
        action_bias={"anger": "suppress"},
    ),
)
result = engine.tick(appraisal, trace=True)
print(result["trace"]["meta_emotions"])
# [{'target': 'anger', 'kind': 'shame', 'intensity': 0.585, ...,
#   'action': 'suppress'}, ...]
```

- **默认关闭**：关闭时 trace 仍是原来的 27 个键，四套 golden 与 L3 A/B 逐位不变。
- **开启方式**：只有 `meta_emotion_enabled=True` 且 `trace=True` 才计算并追加
  `meta_enabled / meta_appraisal / meta_emotions / regulation_bias`；`trace=False` 不计算。
- **中性默认**：无 profile 时不预设“愤怒→羞耻”；标签由四维元评估与通用规则决定。
- **默认只观测**：`expression_gain`、`display_rule`、`tolerance_load_bias` 不进入
  `respond()` 或下一 tick appraisal。
- **L5 可选消费**：`tolerance_meta_load_enabled=True` 且 meta 也开启时，
  `tolerance_load_bias` 作为有界附加负荷（≤0.2）进入 tolerance；默认关闭。
- 同一 anger 的两种二阶态度：
  `uv run python characters/meta_emotion_demo.py` → shame/suppress vs pride/express。
- 25 个 L3.5 测试；可选场景 LLM 表达后全量 **523 tests, OK**。
- 设计根因、文献地图、公式与验收：[`docs/META_EMOTION.md`](docs/META_EMOTION.md)。
- L1 六通道 golden 测试不引入元情绪。

## 关系域长期记忆 v0.2（L4）

`memory.py` 提供按 `(character_id, user_id)` 隔离的长期记忆，不存全量聊天，只存压缩后的卡片、事实、关系摘要和开放循环。

```python
from memory import RelationshipMemoryStore, MemoryService

store = RelationshipMemoryStore("memory.db")
memory = MemoryService(store)

context = memory.build_context("furina", "alice", "今天又被老板骂了。")
# → 有预算的记忆区块，可直接注入 prompt

memory.remember_turn(
    "furina", "alice",
    "我叫小枫，我不喜欢被说教，我答应周五把方案发给你。",
)
```

- 记忆影响 appraisal / relationship / 语气，但**不写 Personality**。
- L4：`appraisal_prior()` 把卡片六维评估压成 ±0.2 先验；
  `engine.tick(appraisal_bias=...)` 只叠加到 `Appraisal`，无 `Appraisal` 时不拉状态。
- 关系摘要每次成功写入后自动刷新；`MemoryWriteGate` 做去重、冲突、置信度与轮次预算。
- 旧 `emotion.db` 记忆可用 `scripts/migrate_legacy_memory.py` 幂等迁移。
- SQLite FTS5 + CJK bigram；无 embedding，无外部依赖。
- 完整设计、信息流图与 GitHub 调研：[`docs/MEMORY_SYSTEM.md`](docs/MEMORY_SYSTEM.md)。
- 测试：`python -m unittest test_memory_system.py test_l4_memory_integration.py -v`。

## 忍耐空间 v0.7 + L5 耐受校准

`tolerance.py` 实现 window of tolerance：负荷只取负性通道相对基线的偏离，`trust / love` 扩展容量而不是进入负荷。L5 默认使用四域容量（anger / fear / intimacy / criticism），有效 capacity 为加权平均；伤疤按事件类型定向压缩。

```python
from tolerance import ToleranceConfig

engine = EmotionEngine(
    ...,
    tolerance_config=ToleranceConfig(domain_scar_load=0.20),
    legacy_tolerance=False,             # True → pre-L5 标量
    tolerance_meta_load_enabled=False,  # True → 消费 meta bias
)
result = engine.tick(appraisal)
print(result["tolerance"])
# {'load': 0.42, 'capacity': 0.86, 'zone': 'regulated',
#  'overload_ratio': 0.49, 'pending_zone': None, 'pending_ticks': 0,
#  'capacity_model': 'multi',
#  'capacity_by_domain': {'anger': ..., 'fear': ...,
#                         'intimacy': ..., 'criticism': ...},
#  'meta_load_bias': 0.0}
```

- `regulated`：正常评估与表达。
- `hyper`：更敏感、更难平复、负性交互增强；下一 tick 负性增益 ×1.5、半衰期 ×2.0。
- `hypo`：过载后的关机，反应变慢；不是“平静”。
- 低负荷不会触发 hypo；区间切换需要两 tick 滞回。
- 伤疤映射：`social_hurt` → anger/fear/criticism；`criticism` → anger/criticism；
  `cold_shoulder` → fear/intimacy；`self_blame` → intimacy；同域取 max。
- `legacy_tolerance=True`：强制 scalar + global max scar，逐值复现 pre-L5。
- meta load 默认关闭；开启后使用上一 tick zone / overload，bias ≤0.2，不累加。
- 快照会保存 `legacy_tolerance` 与 multi 观测字段；`ToleranceConfig` 不持久化。
- L5.1 表达一致性：hyper 使用防御型本地台词与紧张 Live2D 表情；hypo 使用
  关机型台词与低唤醒表情，`_action_tendency` 同时压低趋近与回避。
- 测试：`python -m unittest test_tolerance.py test_engine_tolerance.py test_l5_tolerance.py test_l5_1_expression_dashboard.py -v`。
- 仪表盘关系域记忆隔离：`python -m unittest test_dashboard_memory.py -v`。
- P0 护栏测试：`python -m unittest test_p0_maintainability.py -v`；lint / 覆盖率见 `docs/ARCHITECTURE.md`。
- L5 golden：`python scripts/generate_l5_tolerance_golden.py --check`（legacy 加 `--legacy`）。
- 完整公式、域系数、伤疤映射与限制：[`docs/TOLERANCE.md`](docs/TOLERANCE.md)。

## L6 表达 / JEV / Noul / MCP（v0）

L6 把“开放文本”接到已有的六维 `Appraisal`，并给外部 Agent 一个可直接调用的 MCP 外壳。
所有入口默认关闭，旧调用行为不变。

```python
from engine import respond_llm

result = respond_llm(
    engine, "没想到你居然离开了",
    use_jev=True,        # 规则优先；需要时再叠加 LLM
    auto_boundary=True,  # Noul 文本边界信号
    trace=True,
)
print(result["_jev"])      # appraisal / confidence / evidence / source
print(result["_boundary"]) # violated / confidence / categories
```

- `jev.py`：文本 → 六维 `Appraisal` + confidence + evidence。规则优先，低置信度时可选用 LLM；
  有界 LRU 缓存、批量分块、超时与失败回退。JEV 不写状态、人格或记忆，失败时主链路继续。
- `noul.py`：文本边界信号（亲密 / 命令 / 侮辱 / 胁迫 / 威胁）；亲密权重随 `trust` 变化。
  自动检测只是建议，调用方仍可用 `boundary_violation` 显式覆盖。
- `mcp_server.py`：零依赖 stdio MCP，暴露 `tick` / `recall` / `state`，可直接被
  Claude Code / Codex 调用；记忆仍按 `(character_id, user_id)` 隔离。
- trace 回放：`scripts/replay_trace.py` 与仪表盘 `POST /api/replay` 都使用只读副本，
  不改真实引擎 / 快照 / 记忆。
- 测试：`python -m unittest test_l6_jev.py test_l6_integration.py test_l6_mcp.py -v`。
- 详细契约：[`docs/JEV.md`](docs/JEV.md)、[`docs/MCP.md`](docs/MCP.md)。

## L7 多角色情绪感染（v0 + v0.1 双向 + v0.2 自动关系 + v0.3 群体传播 + v0.4 方向关系 + 网页实验台）

L7 让 A 的六维 `Appraisal` 成为 B 的**有界输入**，同时保持 B 自己的人格、
信任、耐受窗、伤疤与氛围。v0 做单向 A → B；v0.1 增加双向 A ↔ B 与关系强度；
v0.2 从 trust / love 自动推导关系强度；v0.3 增加有向传播网络与多跳衰减；
v0.4 让 A→B / B→A 的关系强度各自独立。所有模式都不共享情绪状态。

```python
from contagion import ContagionEdge, ContagionNetwork, ContagionPair

pair = ContagionPair(engine_a, engine_b, strength=0.7)

# 自动关系强度：从双方 trust / love 推导，并在每次 derive 前刷新
auto_pair = ContagionPair.from_relationship(engine_a, engine_b)

# 单向：A → B
a_result, b_result = pair.tick_pair(
    source_appraisal=a_appraisal,
    target_appraisal=b_appraisal,
    now=now,
)
print(b_result["contagion_bias"])
# {'goal_conduciveness': -0.08, 'expectedness': -0.08, ...}

# 双向：A ↔ B
a_result, b_result = pair.tick_bidirectional(
    source_appraisal=a_appraisal,
    target_appraisal=b_appraisal,
    now=now,
)

# 群体传播：A → B → C，每多一跳乘 hop_decay
network = ContagionNetwork(
    {"A": engine_a, "B": engine_b, "C": engine_c},
    [ContagionEdge("A", "B", 0.8), ContagionEdge("B", "C", 0.6)],
    hop_decay=0.5,
    max_hops=3,
)
biases = network.derive({"A": a_appraisal})
results = network.tick({"A": a_appraisal}, now=now)
```

- 只传四维：`goal_conduciveness` / `expectedness` / `coping_potential` /
  `social_evaluation`；不传 `goal_relevance` / `other_agency`。
- 公式：按中性值归一化后乘 `gain`（默认 0.2），单维截断到 `±cap`
  （默认 0.08，绝对上限 0.2）；中性源不触发感染。
- `strength` 是关系强度：`effective_gain = gain * strength`，默认 1.0，
  范围 [0, 1]；`strength=0` 等价于关闭感染。
- `auto_strength=True` / `from_relationship()`：从双方 `state.trust` /
  `state.love` 推导强度；默认 `trust_weight=0.6`、`love_weight=0.4`，
  love 先按 `[0, 2]` 归一化。自动强度仍受 `cap` 限制。
- `tick_bidirectional()` 先从双方输入 appraisal 算出两个方向的偏置，再 tick；
  结果与顺序无关，没有同 tick 反馈环。
- `ContagionNetwork`：有向边 + 多跳传播；第 1 跳用边强度，之后每跳乘
  `hop_decay`（默认 0.5），最多 `max_hops`（默认 3，上限 8）。同一源到同一
  目标只保留最强路径，多源偏置求和后仍截断到 `±cap`。所有节点先算偏置再 tick。
- `DirectionalRelationship`：A→B 与 B→A 分别由 trust / love / familiarity
  计算，边界条目按数量施加惩罚；平均强度仍可通过 `pair.strength` 读取。
  `directional=True` 与 `auto_strength=True` 互斥。
- `engine.tick(..., contagion_bias=...)` 在 L4 记忆先验之后、`appraise()` 之前
  叠加；默认路径的 result / trace 键集合不变。
- 感染只影响 appraisal，不写状态、记忆或 `Personality`；`ContagionPair` 与
  `ContagionNetwork` 都拒绝共享 `state` / `memory` / `scars` / `tolerance` 的引擎。
- 网页实验台：运行 `python dashboard/server.py` 后打开
  `http://localhost:9020/contagion`；页面提供群体链 / 双向 / 方向关系预设，
  可编辑节点关系、边权与全局参数，执行 1 或 5 tick，并用 JEV 解析事件文本。
  后端为每个节点创建独立 `MemoryStore` 与 `SensitizationStore`，不读写单角色存档。
- 测试：`python -m unittest test_l7_contagion.py test_l7_integration.py test_l7_1_bidirectional.py test_l7_2_relationship_model.py test_l7_3_network.py test_l7_4_directional.py test_contagion_dashboard.py -v`；
  演示：`python scripts/multi_character_demo.py [--bidirectional --strength 0.5 --auto-strength]`、
  `python scripts/contagion_network_demo.py`。
- 详细设计、边界与后续计划：[`docs/L7_MULTI_CHARACTER.md`](docs/L7_MULTI_CHARACTER.md)。

## L8 朋友圈场景（v0 + 网页实验台 + 可选 LLM 表达）

L8 把 L7 的感染能力接成可连续对话的朋友圈：每个角色仍是隔离引擎，
角色之间维护**有向关系**，关系强度决定感染边，回合结束后按听者的
appraisal 更新“听者 → 发言者”的关系。

```python
from social import FriendScene, PeerRelationshipStore, SceneActor

store = PeerRelationshipStore("dashboard/_social_relationships.db")
scene = FriendScene(
    [
        SceneActor("A", "芙宁娜", engine_a, "furina"),
        SceneActor("B", "心海", engine_b, "kokomi"),
    ],
    world_id="friends",
    store=store,
)

# 角色 A 发言：只有 B 收到基于 A→B 关系强度的四维偏置
biases = scene.biases_for("A", appraisal_a)

# 每个角色用各自的引擎 tick 一次，生成台词
# 回合结束后结算 B → A 的关系（用户发言不更新角色间关系）
turn = scene.record_turn("A", {"A": appraisal_a, "B": appraisal_b})
```

- 关系键 `(world_id, source_id, target_id)`；A→B 与 B→A 完全独立。
- 关系字段：`trust` / `love` / `familiarity` / `tension` / `boundaries` /
  `summary` / `interactions`；SQLite 持久化，默认
  `dashboard/_social_relationships.db`。
- 感染边：`directional_relationship_strength(...) × (1 - 0.25 × tension)`；
  关系越好传播越强，tension 越高传播越弱。
- 关系更新公式是 v0 手调常数：valence 取
  `0.5 × goal_conduciveness + 0.5 × social_evaluation`；
  familiarity 随每次互动缓慢增长；越界额外扣 trust、加 tension。
- `FriendScene` 拒绝共享 `state` / `memory` / `scars` / `tolerance`，
  不写 `Personality`，不改变默认 tick，也不新增 golden。
- 场景引擎的情绪状态**不持久化**；重启后关系保留，情绪回到构造时状态。
- 网页：`python dashboard/server.py` → `http://localhost:9020/scene`；
  可选 2–4 个角色、编辑世界 ID 与感染参数、连续对话并查看关系表。
- **可选 LLM 表达（默认关闭）**：`llm.enabled=true` 时每个角色仍由本地
  `respond()` 先 tick，再由 `respond_llm()` 用 DeepSeek / Kimi 替换台词；
  `appraisal` / 感染偏置 / 边界原样传入，失败回退本地模板。密钥只存服务端
  内存，状态 / 响应 / 日志只暴露 `has_api_key` / `has_fallback_key`。
  默认关闭时不改变调用链、返回键集合或六套 golden。
- 测试：`python -m unittest test_l8_scene.py test_scene_dashboard.py test_scene_llm.py -v`
  （15 + 13 + 10，共 38）。
- 详细设计、API 与已知限制：[`docs/L8_SOCIAL_SCENE.md`](docs/L8_SOCIAL_SCENE.md)。

## L8.5 主动联系（v0）

`proactive.py` 是引擎外的确定性调度层：它只回答“现在谁最想主动找用户”，
不推进 `tick()`、不写记忆 / 人格 / 关系。发送时才走一次普通场景回合。

```python
from proactive import ProactiveConfig, ProactiveScheduler

scheduler = ProactiveScheduler(
    scene.actors,
    scene.relationship,
    config=ProactiveConfig(threshold=0.35),
)
# 只读评估：不 tick、不改关系
candidate = scheduler.choose()
if candidate is not None:
    receipt = scheduler.mark_contact(candidate)  # 仅更新调度状态
```

- 紧迫度：`0.35 × 思念 + 0.25 × 静默 + 0.20 × 亲密 + 0.10 × 信任
  + 0.10 × 熟悉 − 0.25 × 紧张 − 0.20 × 未回复`，再截断到 `[0, 1]`。
- 门控顺序：安静时段 → 最小静默 → 冷却 → 每日上限 → 未回复上限 → 阈值。
- 用户发言清空所有角色的未回复计数；角色发言只更新活动时间。
- `FriendScene.snapshot()` 新增 `proactive`；`record_turn()` 自动观察回合。
- 网页 `/scene` 新增检查 / 发送按钮、候选表与主动联系参数；调度器本身不调用
  LLM，发送复用普通场景回合；若场景开启 `llm.enabled`，台词会走 LLM，
  失败回退模板。发送不额外 tick。
- 测试：`python -m unittest test_l85_proactive.py test_proactive_dashboard.py -v`
  （17 + 13，共 30）；可选场景 LLM 表达后全量 **523 tests, OK**。
- 详细设计、API 与限制：[`docs/L8_5_PROACTIVE_CONTACT.md`](docs/L8_5_PROACTIVE_CONTACT.md)。

## 10 通道

| 通道 | 类型 | 触发 | 半衰期 |
|---|---|---|---|
| joy | 快 | 好事发生 | 90min |
| sadness | 快 | 坏事发生 | 180min |
| anger | 快 | 别人造成的伤害 | 120min |
| fear | 快 | 不可控的威胁 | 60min |
| disgust | 快 | 负面社会评价 | 90min |
| surprise | 快 | 意外事件 | 30min |
| **trust** | **慢** | 正面交互累积，背叛腰斩，被 love 引力线牵引 | 不衰减 |
| **love** | **慢** | 时间+正面交互累积，信任缺口大时被侵蚀 | 不衰减 |
| **longing** | **缺失** | 离线时 love × log(时间) 生长，在线时消退 | 半小时 |
| **guilt** | **自责** | self_agency > 0.3 且造成伤害 | 180min |

## 关键机制

### 弹性衰减
```
effective_T½ = T½ / (1 + |value - baseline| / baseline)
```
偏离基线越远，回弹越快。无硬上限——用自然饱和 + log₁₀ 感知压缩替代。

### 双时钟
```
在线 tick(): 逐帧处理事件
离线 wake(): 休眠后一次性结算，各通道独立压缩
  anger ×0.08 — 睡醒基本不气
  sadness ×0.30 — 悲伤穿透睡眠
  love ×0.98 — 几乎不打折
```

### 记忆-情绪耦合
- **Arousal 驱动存储**（非 Valence）— Diamond 2007 / Bowen 2016
- **30 分钟固化窗口** — Nielson 2007
- **Arousal 标记召回**（非情绪一致性）— York 2025
- **Saga 效应** — 长期记忆每 24h 微拉基线

### 触发敏感化（旧伤疤）
- **Kindling** — 同模式 3 次后门槛降 8%/次，最敏感 +60% — Post 1992
- **贝叶斯威胁** — 负面信号 5× 权重，需 30 次正面才复位 — MindLAB 2024
- **去情境化恐惧** — 原始模式泛化到相似信号 — Neudert 2024

## 角色蒸馏

`characters/kokomi.py` — 珊瑚宫心海（高尽责·社交焦虑的军师）
`characters/columbina.py` — 哥伦比娅（极内向·孤独清冷的月神）

wiki → OCEAN → baseline → appraisal trigger 映射。
两个角色在同一引擎上跑出完全不同的曲线——验证了引擎的区分度。

## 论文基础

| 决策 | 论文 |
|---|---|
| 并行匹配流替代 PAD | Jennings (2025) |
| 混合情绪互不消灭 | Vanderbilt Emotional Blends |
| Arousal 驱动记忆固化 | Diamond (2007) / Bowen (2016) |
| 30min 固化窗口 | Nielson (2007) |
| 人格 30 岁后稳定 | Ones, Stanek & Dilchert (2024) |
| 人格三层模型 | McAdams |
| Kindling 敏感化 | Post (1992) / Kendler (2000) |
| 贝叶斯威胁模型 | MindLAB Neuroscience (2024) |
| 抑郁现实主义 | Sci Reports (2024) |

## License

MIT · 枫骅 2026
