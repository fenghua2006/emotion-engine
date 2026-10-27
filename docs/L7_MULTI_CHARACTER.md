# L7：多角色情绪感染（v0 + v0.1 双向 + v0.2 自动关系 + v0.3 群体传播 + v0.4 方向关系）

> 目标：让 A 的 `Appraisal` 成为 B 的有界输入，而不是让两个角色共享同一份情绪状态。
>
> 代码：`contagion.py` · 测试：`test_l7_contagion.py` + `test_l7_integration.py` + `test_l7_1_bidirectional.py` + `test_l7_2_relationship_model.py` + `test_l7_3_network.py` + `test_l7_4_directional.py` · 演示：`python scripts/multi_character_demo.py` / `python scripts/contagion_network_demo.py`

L7 v0 是**单向**感染：A 的事件评估 → B 的 appraisal 先验。B 仍使用自己的
`Personality`、trust / love、耐受窗、伤疤和氛围计算情绪。

L7.1 增加**双向**感染与**关系强度**：`tick_bidirectional()` 在 tick 前分别
从 A、B 本轮的输入 appraisal 求出两个方向的偏置，再各自 tick；`strength`
乘在 `gain` 上，可作为角色关系强度。

L7.2 增加**自动关系模型**：`relationship_strength()` 从 trust / love 推导
单个角色的关系强度，`ContagionPair.from_relationship()` / `auto_strength=True`
在每次 `derive()` 前从双方当前状态刷新 `strength`。自动强度仍然只是
`gain` 的乘数，不改变四维偏置公式和 cap。

L7.3 增加**群体传播**：`ContagionNetwork` 接收多个独立引擎与有向边，
支持直接感染、多跳传播链和 `hop_decay` 衰减。所有节点的偏置在任何
tick 之前算完，同一 tick 内仍然没有反馈环。

L7.4 增加**方向性关系模型**：`DirectionalRelationship` 与
`directional=True` 分别计算 A → B、B → A 的强度，纳入该方向发起角色的
trust / love / familiarity 与 boundaries；方向强度不再被 pair 平均值抹平。

---

## 1. 设计边界

```text
单向 A → B

A 的 event Appraisal
        │
        ▼
derive_contagion_bias()      四维、有界、确定性
        │
        ▼
B 的 tick(contagion_bias=...)
        │  L4 记忆先验之后、appraise() 之前
        ▼
B 的 appraise → gate → tolerance → scar → atmosphere → state

双向 A ↔ B（L7.1）

A 的输入 Appraisal ──derive──▶ A ← B 偏置 ┐
B 的输入 Appraisal ──derive──▶ B ← A 偏置 ┤
                                           ├─ 两个偏置都算完后才 tick
A 的 tick(contagion_bias=B→A) ◀────────────┘
B 的 tick(contagion_bias=A→B)

群体传播（L7.3）

A 的输入 Appraisal ──derive──▶ B 偏置 ──edge──▶ C 偏置
                                  │ 多跳乘 hop_decay
                                  ▼
                        所有节点偏置算完后统一 tick

方向性关系（L7.4）

A 的 relation snapshot（trust/love/familiarity/boundaries）
        └──▶ strength A → B ──▶ 只影响 B 收到的偏置

B 的 relation snapshot
        └──▶ strength B → A ──▶ 只影响 A 收到的偏置
```

双向模式的关键：两个偏置都来自**本轮的输入 appraisal**，不在 tick 之间
互相读取输出，因此不会形成同一 tick 内的反馈环，也不依赖先 tick 谁。
自动 / 方向关系强度同样在任何 tick 之前读取，不会被源角色本 tick 的
状态更新影响。

硬约束：

- 只改 `Appraisal`，不直接改 `EmotionalState` / `Personality` / `CharacterCore`。
- 不写记忆；不存整段聊天；不改变 `(character_id, user_id)` 隔离。
- 源和目标不得共享 `state` / `memory` / `scars` / `tolerance`；共享只读
  `Personality` 允许。
- 默认路径（不传 `contagion_bias` / 不用双向）的 result、trace 键集合与六套
  golden 不变。
- 感染不进入快照、MCP 工具或 dashboard；它是每 tick 的输入，不是持久状态。

## 2. 传输哪四维

只传四个跨角色有意义的维度：

| 维度 | 中性值 | 方向 |
|---|---:|---|
| `goal_conduciveness` | 0.0 | 正 = 好事，负 = 障碍 |
| `expectedness` | 0.5 | 低 = 更意外，高 = 更预期 |
| `coping_potential` | 0.5 | 低 = 更无力，高 = 更可控 |
| `social_evaluation` | 0.0 | 正 = 被肯定，负 = 被否定 |

不传：

- `goal_relevance`：A 的目标相关性不等于 B 的目标相关性；
- `other_agency`：跨角色后“谁导致的”语义不稳定。

`expectedness` / `coping_potential` 先按中性值归一化到 `-1~1`：

```text
normalized =
    goal_conduciveness  → v
    expectedness        → (v - 0.5) * 2
    coping_potential    → (v - 0.5) * 2
    social_evaluation   → v

bias_i = clamp(gain * normalized_i, -cap, +cap)
```

默认：

```text
gain = 0.2
cap  = 0.08        # 单维
MAX_CONTAGION_CAP = 0.2
```

`gain` 限制在 `[0, 1]`，`cap` 限制在 `[0, 0.2]`。中性源返回空字典；
缺失 / 非有限 / 非法值被跳过。默认参数下四个维度最多各移动 0.08，
足够产生可观察的情绪变化，又不会覆盖 B 自己的评估。

## 3. 引擎接入

`EmotionEngine.tick()` 新增末尾参数：

```python
result = engine.tick(
    appraisal,
    contagion_bias={
        "goal_conduciveness": -0.08,
        "expectedness": -0.08,
    },
    trace=True,
)
```

顺序固定为：

```text
L4 memory bias → L7 contagion bias → appraise() → gate → tolerance
→ scar → atmosphere → state write
```

注意：`grow_slow_channels()` 在 appraisal 阶段之前，因此**同一 tick 的
love / trust 生长仍看原始 appraisal**。这是保留 tick 顺序契约的结果，
不是感染失效；感染影响本 tick 的情绪评估和后续状态。

返回与 trace：

- 只有感染路径实际运行且存在 `Appraisal` 时，`result["contagion_bias"]`
  才出现；值是实际生效的逐维 delta（4 位小数）。
- trace 追加且仅追加三个键：`contagion_enabled`、`contagion_applied`、
  `appraisal_before_contagion`。默认 trace 仍是 27 键。
- L4 + L7 同时启用时，记忆和感染字段同时出现；`appraisal_before_contagion`
  是记忆先验之后的六维值。

## 4. `ContagionPair`

```python
from contagion import ContagionPair

pair = ContagionPair(
    engine_a, engine_b,
    strength=0.7,          # 显式关系强度：effective_gain = gain * strength
)

# 自动关系模型（L7.2）：从双方 trust / love 推导并刷新 strength
relationship_pair = ContagionPair.from_relationship(
    engine_a, engine_b,
    trust_weight=0.6,
    love_weight=0.4,
)

# 单向 A → B
a_result, b_result = pair.tick_pair(
    source_appraisal=a_appraisal,
    target_appraisal=b_appraisal,
    now=now,
)
print(b_result["contagion_bias"])

# 双向 A ↔ B（L7.1）
a_result, b_result = pair.tick_bidirectional(
    source_appraisal=a_appraisal,
    target_appraisal=b_appraisal,
    now=now,
)
print(a_result["contagion_bias"])  # B → A
print(b_result["contagion_bias"])  # A → B
```

- `tick_pair()`：先 tick A，再 tick B；同一时钟，A 先 B 后。目标偏置在
  任何 tick 之前计算，所以自动 / 方向强度不会读取 A 刚更新后的状态。
- `tick_bidirectional()`：先从 A、B 的**输入 appraisal** 分别算出 B → A 与
  A → B 的偏置，再 tick A、B；结果与 tick 顺序无关，也没有同 tick 反馈环。
- A 传给 B 的是 **A 本轮的输入 appraisal**，不是 A 的最终通道状态；
  A 的 L4 记忆先验不会隐式转给 B。双向模式同理。
- `tick_target()`：只 tick B；有 `source_appraisal` 时叠加感染。
- `source_appraisal` 或 `target_appraisal` 缺失时，行为与普通 `tick()` 一致；
  双向模式下任一缺失会关闭两个方向。
- `strength` 限制在 `[0, 1]`，默认 `1.0`；`strength=0` 等价于关闭感染。
  显式模式不会读取 trust / love。
- `auto_strength=True` / `ContagionPair.from_relationship()`：从双方当前
  `state.trust` / `state.love` 推导关系强度，每次 `derive()` 前刷新。
  `strength` 参数在自动模式下会被覆盖。
- `directional=True` / `ContagionPair.from_directional_relationship()`：
  A → B 与 B → A 各自读取发起角色的关系快照；详见 4.3。
- 构造时拒绝同一 engine，以及共享 `state` / `memory` / `scars` /
  `tolerance` 的两个 engine。

### 4.1 自动关系强度（L7.2）

单个角色：

```text
normalized_love = clamp(love, 0, 2) / 2
strength_i = clamp(trust_weight * clamp(trust, 0, 1)
                   + love_weight * normalized_love, 0, 1)
```

默认 `trust_weight=0.6`、`love_weight=0.4`；权重会先归一化。pair 的
`strength` 是两个角色 `strength_i` 的平均值：

```text
pair.strength = (strength_A + strength_B) / 2
```

- 缺失 / NaN / Inf / bool 的 trust 或 love 按 0 处理，避免脏数据放大感染。
- 高 trust / love 只放大 `gain`，仍然受 `cap` 与 `MAX_CONTAGION_CAP` 限制。
- 默认 `auto_strength=False`，显式 `strength` 路径逐位不变。
- 自动模型是**双向对称**的 pair 强度；若需要 A→B 与 B→A 使用不同强度，
  使用 4.3 的方向性关系模型，或分别构造两个单向 `ContagionPair`。

### 4.2 群体传播（L7.3）

```python
from contagion import ContagionEdge, ContagionNetwork

network = ContagionNetwork(
    {"A": engine_a, "B": engine_b, "C": engine_c},
    [
        ContagionEdge("A", "B", 0.8),
        ContagionEdge("B", "C", 0.6),
    ],
    hop_decay=0.5,   # 第二跳起每跳再乘一次
    max_hops=3,
)

biases = network.derive({"A": a_appraisal})
# {"B": {...}, "C": {...}}

results = network.tick(
    {"A": a_appraisal, "B": b_appraisal},
    now=now,
    trace=True,
)
# {"A": result_a, "B": result_b, "C": result_c}
```

传播权重：

```text
path_weight(hop=1) = edge.strength
path_weight(hop=n) = product(edge.strength) * hop_decay ** (n - 1)

node_bias_d = clamp(sum(origin_bias_d * path_weight), -cap, +cap)
```

- 每个有输入 `Appraisal` 的节点都可以是 origin；一个节点可同时收到直接
  与间接贡献。
- 同一 `(origin, target)` 只保留最强路径，避免环路重复累加；`max_hops`
  是硬上限，默认 3，最大 8。
- 所有贡献汇总后每个维度重新 clamp 到 `±cap`；网络不会突破单对上限。
- 所有偏置在 tick 前算完；节点按 mapping 插入顺序 tick，结果与边的
  输入顺序无关。
- `ContagionEdge` 拒绝自环；网络拒绝重复有向边、未知端点、重复 engine，
  以及任意两个节点共享 `state` / `memory` / `scars` / `tolerance`。

### 4.3 方向性关系模型（L7.4）

```python
from contagion import ContagionPair, DirectionalRelationship

pair = ContagionPair.from_directional_relationship(
    engine_a,
    engine_b,
    source_relationship=DirectionalRelationship(
        trust=0.9, love=1.4, familiarity=0.8,
        boundaries=("不深夜打扰",),
    ),
    target_relationship=DirectionalRelationship(
        trust=0.2, love=0.3, familiarity=0.1,
    ),
)

pair.source_to_target_strength   # A → B
pair.target_to_source_strength   # B → A
```

单个方向：

```text
trust_n         = clamp(trust, 0, 1)
love_n          = clamp(love, 0, 2) / 2
familiarity_n   = clamp(familiarity, 0, 1)

base = normalized(trust_weight) * trust_n
     + normalized(love_weight) * love_n
     + normalized(familiarity_weight) * familiarity_n

boundary_factor = 1 / (1 + boundary_penalty * boundary_count)
strength = clamp(base * boundary_factor, 0, 1)
```

默认权重 `trust 0.6 / love 0.4 / familiarity 0.2`（归一化后使用），
`boundary_penalty=0.25`。`relationship` 可以是带同名字段的任意对象
（包括 `memory.RelationshipState`）或 Mapping；缺失字段按 0。

- `directional=True` 与 `auto_strength=True` 互斥。
- 未显式传入关系快照时，方向强度回退到 `engine.relationship`，再回退到
  `engine.state` 的 trust / love；此时 familiarity / boundaries 按 0。
- 单向 `tick_pair()` 使用 A → B；`tick_bidirectional()` 的 A 侧使用
  B → A，B 侧使用 A → B。
- `pair.strength` 在方向模型下只是两侧平均值，便于观测；实际每个方向
  使用各自的 `effective_gain_for(...)`。
- 当前快照不额外存历史；调用方更新 trust / love / familiarity /
  boundaries 后，下一次 `derive()` 自动反映变化。

`respond()` / `respond_llm()` 也新增可选 `contagion_bias` 并原样透传；
LLM 仍然只写 `utterance`，不写回通道或人格。

## 5. 隔离与持久化

- 记忆按 `(character_id, user_id)` 隔离；感染不调用记忆写入。
- 感染是每 tick 输入，不写入快照；`SNAPSHOT_VERSION` 仍为 2。
- 记忆 schema 不变；`MEMORY_SCHEMA_VERSION` 仍为 1。
- 旧版 `MemoryStoreDB`、伤疤存储没有角色身份，因此多角色场景不要共享
  这些对象；`ContagionPair` 与 `ContagionNetwork` 都会直接拒绝共享。
- 共享 `Personality` 是允许的，因为感染只读人格、不写人格；但若希望两个
  角色有不同底色，应传入不同 `Personality`。
- 方向性关系快照只读；自动 / 方向模型都不会写回 `state` 或关系存储。

## 6. 演示

```bash
python scripts/multi_character_demo.py
python scripts/multi_character_demo.py --gain 0.3 --cap 0.1
python scripts/multi_character_demo.py --bidirectional --strength 0.5
python scripts/multi_character_demo.py --auto-strength --bidirectional
python scripts/contagion_network_demo.py
```

`multi_character_demo.py` 创建 A（高外向 / 高神经质）与 B（内向 / 高宜人），
让 A 经历负面事件、B 只做旁观评估。默认对比 B 在有 / 无单向感染下的
joy / sadness / fear / trust；`--bidirectional` 同时对比 A 和 B 的双向变化，
`--strength` 缩放关系强度，`--auto-strength` 从双方 trust / love 自动推导强度。

`contagion_network_demo.py` 演示 A → B → C 的两跳传播与 hop 衰减，
再演示 A → B / B → A 强度不对称的方向性关系模型。

## 7. 测试

```bash
python -m unittest test_l7_contagion.py test_l7_integration.py \
  test_l7_1_bidirectional.py test_l7_2_relationship_model.py \
  test_l7_3_network.py test_l7_4_directional.py -v
```

覆盖：

- 四维常量、中性值、正负方向和默认上限；
- 自定义 gain / cap / strength、非法输入清洗、NaN / Inf 防护；
- 同一 engine / 共享可变子结构的拒绝；
- 共享 `Personality` 不被修改；
- 中性源与普通 tick 逐值一致；
- 直接 tick、L4 + L7 叠加、trace 条件键、result 条件键；
- 无 appraisal 时 no-op；
- `respond()` / `respond_llm()` 透传与本地回退；
- `tick_pair()` 让 B 变化但不影响 A；
- 双向两个方向都生效、镜像对称、顺序无关、单侧中性 / 缺失 appraisal；
- 双向的 L4 记忆先验、边界标志、trace 三键与 `tick_pair()` 单向回归；
- trust / love → strength 公式、归一化、权重、越界与非法输入；
- 自动强度的双方平均、刷新、derive 前更新、零关系关闭感染、cap 与隔离；
- 网络直接边与 `ContagionPair` 一致、两跳衰减、hop_decay=0、
  max_hops 限制、多源汇总后 clamp、环路不放大、边顺序无关；
- 网络边校验、重复 engine / 共享状态拒绝、节点 tick 顺序与 trace；
- 方向强度公式、familiarity / boundaries 衰减、权重归一化与非法输入；
- A→B / B→A 独立、显式关系覆盖 state、刷新、互斥参数、
  方向模型与网络边权组合；
- 快照 / 记忆 schema 版本不变，感染不额外写记忆。

当前：`test_l7_contagion.py` 14 个，`test_l7_integration.py` 13 个，
`test_l7_1_bidirectional.py` 14 个，`test_l7_2_relationship_model.py` 14 个，
`test_l7_3_network.py` 22 个，`test_l7_4_directional.py` 21 个，
共 98 个 L7 测试；全量 436 tests OK；ruff clean；coverage 87%（branch，
gate 80）；六套 golden 无 diff。

## 8. 限制与下一步

- **双向是输入级对称**：L7.1 的 `tick_bidirectional()` 支持同一 tick 内
  A ↔ B，但每个方向只读对方本轮的输入 appraisal，不读取对方刚更新的情绪
  输出；跨轮反馈回路仍由调用方控制。
- **无相关性门控**：`goal_relevance` 不参与传输；低相关事件也可能带来
  小幅感染。后续可用 B 自己的 relevance 做门控。
- **自动 pair 强度仍然对称**：L7.2 的 `strength` 是双方 trust / love 推导出的
  pair 级平均值；需要不对称时使用 L7.4 的方向模型。
- **方向强度是当前快照**：L7.4 只读当前 trust / love / familiarity /
  boundaries，不单独存互动历史、依恋风格或事件序列；调用方更新快照后，
  下一次 derive 才反映变化。
- **boundaries 只是计数衰减**：每条边界按统一 penalty 降低强度，不区分
  边界类型、严重程度或是否被违反；后续可接关系记忆的结构化边界。
- **网络衰减是固定公式**：边权 × `hop_decay`，同一 (origin, target) 只保留
  最强路径；没有学习、最短路径优化或角色群体的全局归一化。
- **感染源是输入 appraisal**：不是 A 的最终情绪；若需要“看到 A 的表情后
  被感染”，应由上游先构造 A 的表达 appraisal，再调用本模块。
- **版本说明**：设计纲领中的 v0.8 曾指“记忆显式化”，而 ROADMAP 当前
  v0.8 指多角色交互；本次按 ROADMAP 实现 L7 = v0.8 多角色
  v0 + v0.1 + v0.2 + v0.3 + v0.4，不声称完成设计纲领的全部 v0.8 记忆目标。
