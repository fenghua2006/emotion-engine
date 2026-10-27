# L7 v0：多角色情绪感染

> 目标：让 A 的 `Appraisal` 成为 B 的有界输入，而不是让两个角色共享同一份情绪状态。
>
> 代码：`contagion.py` · 测试：`test_l7_contagion.py` + `test_l7_integration.py` · 演示：`python scripts/multi_character_demo.py`

L7 v0 是**单向**感染：A 的事件评估 → B 的 appraisal 先验。B 仍使用自己的
`Personality`、trust / love、耐受窗、伤疤和氛围计算情绪。双向同时感染、
群体传播与角色关系图不在 v0 范围内。

---

## 1. 设计边界

```text
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
```

硬约束：

- 只改 `Appraisal`，不直接改 `EmotionalState` / `Personality` / `CharacterCore`。
- 不写记忆；不存整段聊天；不改变 `(character_id, user_id)` 隔离。
- 源和目标不得共享 `state` / `memory` / `scars` / `tolerance`；共享只读
  `Personality` 允许。
- 默认路径（不传 `contagion_bias`）的 result、trace 键集合与六套 golden 不变。
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

pair = ContagionPair(engine_a, engine_b)  # 单向 A → B

a_result, b_result = pair.tick_pair(
    source_appraisal=a_appraisal,
    target_appraisal=b_appraisal,
    now=now,
)
print(b_result["contagion_bias"])
```

- `tick_pair()`：先 tick A，再 tick B；同一时钟，A 先 B 后。
- A 传给 B 的是 **A 本轮的输入 appraisal**，不是 A 的最终通道状态；
  A 的 L4 记忆先验不会隐式转给 B。
- `tick_target()`：只 tick B；有 `source_appraisal` 时叠加感染。
- `source_appraisal` 或 `target_appraisal` 缺失时，行为与普通 `tick()` 一致。
- 构造时拒绝同一 engine，以及共享 `state` / `memory` / `scars` /
  `tolerance` 的两个 engine。

`respond()` / `respond_llm()` 也新增可选 `contagion_bias` 并原样透传；
LLM 仍然只写 `utterance`，不写回通道或人格。

## 5. 隔离与持久化

- 记忆按 `(character_id, user_id)` 隔离；感染不调用记忆写入。
- 感染是每 tick 输入，不写入快照；`SNAPSHOT_VERSION` 仍为 2。
- 记忆 schema 不变；`MEMORY_SCHEMA_VERSION` 仍为 1。
- 旧版 `MemoryStoreDB`、伤疤存储没有角色身份，因此多角色场景不要共享
  这些对象；`ContagionPair` 会直接拒绝共享。
- 共享 `Personality` 是允许的，因为感染只读人格、不写人格；但若希望两个
  角色有不同底色，应传入不同 `Personality`。

## 6. 演示

```bash
python scripts/multi_character_demo.py
python scripts/multi_character_demo.py --gain 0.3 --cap 0.1
```

演示创建 A（高外向 / 高神经质）与 B（内向 / 高宜人），让 A 经历负面事件、
B 只做旁观评估，对比 B 在有 / 无感染下的 joy / sadness / fear / trust。

## 7. 测试

```bash
python -m unittest test_l7_contagion.py test_l7_integration.py -v
```

覆盖：

- 四维常量、中性值、正负方向和默认上限；
- 自定义 gain / cap、非法输入清洗、NaN / Inf 防护；
- 同一 engine / 共享可变子结构的拒绝；
- 共享 `Personality` 不被修改；
- 中性源与普通 tick 逐值一致；
- 直接 tick、L4 + L7 叠加、trace 条件键、result 条件键；
- 无 appraisal 时 no-op；
- `respond()` / `respond_llm()` 透传与本地回退；
- `tick_pair()` 让 B 变化但不影响 A；
- 快照 / 记忆 schema 版本不变，感染不额外写记忆。

当前：`test_l7_contagion.py` 14 个，`test_l7_integration.py` 13 个；
全量 365 tests OK；ruff clean；coverage 87%（branch，gate 80）；六套 golden 无 diff。

## 8. 限制与下一步

- **单向**：v0 不做双向反馈回路；需要时由调用方组合两个 `ContagionPair`
  并明确顺序。
- **无相关性门控**：`goal_relevance` 不参与传输；低相关事件也可能带来
  小幅感染。后续可用 B 自己的 relevance 或角色间关系强度做门控。
- **无角色关系强度**：v0 的 gain / cap 是全局参数；后续可按信任、亲密、
  依恋或角色配置逐对设置。
- **感染源是输入 appraisal**：不是 A 的最终情绪；若需要“看到 A 的表情后
  被感染”，应由上游先构造 A 的表达 appraisal，再调用本模块。
- **无群体传播**：多角色网络、传播链和衰减留给 L7.1+。
- **版本说明**：设计纲领中的 v0.8 曾指“记忆显式化”，而 ROADMAP 当前
  v0.8 指多角色交互；本次按 ROADMAP 实现 L7 = v0.8 多角色 v0，不声称
  完成设计纲领的全部 v0.8 记忆目标。
