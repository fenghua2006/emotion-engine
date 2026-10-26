# 增量开发计划（L0–L6 + L3.5）

> 主开发目录：`C:\Users\Administrator\Desktop\emotion-engine`。后续所有修改、测试、提交都在这里。
> D 盘 `D:\Users\Administrator\Desktop\emotion-engine` 是 2026-10-25 快照，停止更新。
>
> 原则：不再一次性往上叠功能。先把底层情绪线锁住，再逐层加。
> 已实现的功能不删除；增量开发 = 补契约、补回归、再改行为。

## 分层

| 层 | 范围 | 状态 | 完成标志 |
|---|---|---|---|
| **L0** | 核心提取与可观测性 | ✅ 2026-10-25 | 旧测试全过；`trace` 不改变计算 |
| **L1** | 六条快通道 | ✅ 2026-10-26（L1.1 修复交互激活） | 46 条事件双 golden 不漂移；22 + 11 个测试通过 |
| **L2** | trust / love / longing / guilt | ✅ 2026-10-26 | 19 条事件双 golden；32 个 L2 测试；trust 30 天不发散；guilt 门槛锁定 |
| **L3** | 氛围与伤疤 | ⏳ | 顺序修正可 A/B；伤疤有对照 |
| **L3.5** | 元情绪：二阶评估与调节意图 | ⏳ 设计已记录 | 不新增通道；关闭时基础曲线不变 |
| **L4** | 关系域记忆 | ⏳ | 记忆不写 Personality；预算可控 |
| **L5** | 忍耐空间 | ⏳ 已实现待校准 | 参数有 golden set 依据 |
| **L6** | 表达 / LLM / JEV / MCP | ⏳ | 全链路可回退、可观测 |

## L0：核心情绪线 ✅

交付：

- `core.py`：通道、`EmotionalState`、衰减、回弹、饱和、慢通道、六维评估、门控、交互矩阵。
- `StepTrace`：`before → after_decay → after_slow_channels → after_appraisal → after_atmosphere → after_interaction → final`。
- `test_core_line.py`：28 个测试。
- `engine.py`：保留组合根与旧 import 路径；`tick(trace=True)` 只增加观察数据。

验收：

```text
L0 时 69 tests, OK（L1.1 后全量 102 tests, OK）
engine.py / test_iloveyou.py / characters/*.py 全部退出码 0
```

边界：

- `core.py` 不导入 `engine` / `tolerance` / `memory` / SQLite / 网络。
- 后续层读取 `StepTrace`，提供有界偏置或控制系数，不改写核心公式。
- 会改变曲线的参数必须有开关、默认关闭、前后对比测试。

详细契约见 [`CORE.md`](CORE.md)。

### 已知顺序问题（L0 刻意未修）

1. 氛围门控在 state 写入之后才修改 `gated`，本 tick 实际不生效。
2. shock 密度在 shock 检测之前采样，`_recent_shocks` 长期为 0。

两条都记录在 `StepTrace.notes`，归 L3 修正。

## L1：六条快通道 ✅

目标：joy / sadness / anger / fear / disgust / surprise 作为可独立验证的基础线。

交付：

- `fastline.py`：固定人格、固定 trust/love、固定 10 分钟步长的六通道参考线。
- `l1_events.py`：46 条冻结事件（角色 31 + engine demo 3 + synthetic 12）。
- `fixtures/l1_golden.json`：修复后 isolated + sequence golden。
- `fixtures/l1_golden_legacy.json`：v0.7 绝对阈值交互的对照 golden。
- `test_l1_fast_channels.py`：22 个测试，golden 误差 < `1e-9`。
- `test_interaction_activation.py`：11 个交互语义与引擎集成测试。
- `scripts/generate_l1_golden.py`：生成 / `--check` / `--legacy`。
- `docs/L1_FAST_CHANNELS.md`：范围、参考线、修复记录与验收。

验收结果：

```text
22 L1 tests, OK
11 interaction tests, OK
102 tests total, OK
```

### L1.1：交互激活修复（2026-10-26）

根因：`apply_interactions()` 用绝对通道值 0.05 做门槛，surprise 基线 0.10、
disgust 基线 0.06 被当成“正在发生的情绪”，造成空闲交互地板和凭空 joy / fear。

修复：

1. 传入 `baseline` 时，源激活 = `max(0, value - baseline)`，超过 0.05 才参与交互。
2. 快通道放大规则要求目标共激活：惊讶只能放大已存在的喜悦/恐惧。
3. 抑制规则不要求目标共激活：愤怒仍可压低基线信任。
4. 默认 `relative_to_baseline`；`legacy_interactions=True` 保留旧行为。

对照结果：

- 空闲 300 tick：joy 0.4220 → 0.3500；fear 0.1920 → 0.1050；anger 0.1239 → 0.0550。
- 纯负面事件 joy：0.4119 → 0.3500（基线）。
- 46 条事件最大差值：fear 0.173943、joy 0.115717、anger 0.066675。
- 变化集中在正面事件：旧行为给它们额外加了无来源 fear / anger。

其他说明：

- L1 参考线只保留六通道之间的交互；slow → fast 交互留给 L2。
- 生产引擎里 trust 高于基线时仍会压低 fear，这是 L2 的 slow→fast 调制，
  不是空闲交互地板。
- guilt 暂不进入 L1 验收，留给 L2。
- 元情绪不进入 L1；L1 只锁基础六通道，元情绪留给 L3.5。
- 剩余已知问题：dominant 沿用引擎规则（最高原始值且 > 0.3），joy 基线 0.35
  造成基线偏差，未在 L1 另造规则。

不做：不接 JEV；不改 tolerance；不动 memory；不拆包；不改交互权重。

## L2：慢通道与缺失通道 ✅

目标：trust / love / longing / guilt 作为可独立验证的慢通道；slow → fast
交互有明确基线口径，且不会在空闲 tick 把快通道拉离人格基线。

交付：

- `slowline.py`：固定人格、固定时钟的十通道参考线，在线走 `tick()` 路径，
  离线走 `wake()` 路径。
- `l2_events.py`：19 条冻结事件，覆盖关系正负事件、guilt 门槛、越界、
  离线 2h / 24h / 8d / 30d、长期缺席后的修复。
- `fixtures/l2_golden.json`：当前 L2 的 isolated + sequence golden。
- `fixtures/l2_golden_legacy.json`：旧慢通道 + 绝对阈值交互的对照 golden。
- `test_l2_slow_channels.py`：32 个测试。
- `scripts/generate_l2_golden.py`：生成 / `--check` / `--legacy`。
- `docs/L2_SLOW_CHANNELS.md`：决策、数据、验收与 legacy 开关。

核心语义：

1. trust 向 `trust_target(love)` 收敛；背叛腰斩；长期离线微降。
2. love 信任健康时生长，缺口大时被侵蚀；正面事件只按**本区间新增**计数。
3. longing 离线生长、在线消退，受 `love × 2` 上限约束。
4. guilt 只在 `self_agency > 0.3` 时激活；0.3 及以下严格为 0。
5. trust / love 分别 clamp 到 `[0, 1]` / `[0, 2]`；`love → trust` 与
   `guilt → trust` 的累计增量受 `trust_target(love)` 封顶。
6. 慢通道不参与快通道半衰期，也不在 tolerance load 中重复计数。
7. 快照持久化慢通道状态与两个 legacy 开关，恢复后曲线连续。

基线决策：

| 通道 | 交互基线 |
|---|---|
| trust | `trust_target(love)` |
| love | 人格基线 |
| 六快通道 + guilt | 人格基线 |

为什么不统一用人格基线：trust 在正常关系里会长到目标；若把这段正常信任
当成“高于人格基线的额外信任”，空闲 tick 会持续压 fear。实测 300 tick
会把 fear 从 0.105 拉到 0.0。`gate_appraisal()` 中 trust 对负性通道的
绝对缓冲保留，交互矩阵只补充“高于关系均衡线”的调制。

legacy 开关：

- `legacy_slow_channels=True`：guilt 无门槛；love 用累计正面事件；
  trust 交互回到人格基线且不设 target 上限。
- `legacy_interactions=True`：交互矩阵回到 v0.7 绝对阈值语义。
- 两者同时为 `True` 可近似复现 L1.1 之前的旧曲线。

验收结果：

```text
32 L2 tests, OK
134 tests total, OK
四套 golden 全部通过：L2 current / L2 legacy / L1 current / L1 legacy
30 天模拟：trust 0.872741 = trust_target，love 1.959433
纯外部归因（self_agency=0.05）：guilt = 0
空闲 300 tick：六快通道精确回到人格基线
```

L2 没有进入 L1 参考线；L1 的 46 条事件与两套 golden 均未改变。
详细记录见 [`L2_SLOW_CHANNELS.md`](L2_SLOW_CHANNELS.md)。

## L3：氛围与伤疤

1. 把 atmosphere 调制移到写入 state 之前；保留兼容开关。
2. 先检测 shock，再更新氛围；shock 密度不再恒为 0。
3. atmosphere 限制在 `[-1, 1]`。
4. 伤疤放大限定 sadness / fear / anger，上限 60%。
5. 增加无伤疤 / 有伤疤对照测试。
6. 用 `StepTrace` 记录修正前后差异。

验收：默认可选兼容 / 修正模式；修正版下紧绷氛围提高后续负面反应；旧测试在兼容模式下全过。

## L3.5：元情绪

目标：让引擎能回答“角色对这个情绪本身是什么态度”，而不是只回答“角色有多愤怒”。

核心约束：

- 元情绪是**二阶过程**，不是第 11 个基础通道。
- `target` 指向一个 primary channel；`kind` 是 shame / guilt / pride / contempt / compassion / interest / fear 等标签。
- 元评估四维：`goal_conduciveness`、`social_acceptability`、`controllability`、`identity_congruence`。
- 输出只做有界偏置：表达、调节意图、耐受负荷；不改写 `core.py` 公式，不写 `Personality`。
- 角色稳定倾向放 `MetaEmotionProfile`，瞬时状态放 `MetaEmotionState`。

工作：

1. 定义 `MetaEmotion` / `MetaAppraisal` / `MetaEmotionProfile`。
2. 实现 `meta_appraise()` 与 `meta_regulate()`。
3. `StepTrace` 增加 `meta_appraisal` / `meta_emotions` / `regulation_bias`。
4. 默认关闭；开启后不改变旧曲线，只增加表达/调节/耐受偏置。
5. 至少一个角色 demo 对比“愤怒→羞耻→压抑”与“愤怒→骄傲→表达”。

验收：

- 关闭元情绪层时，L1/L2/L3 golden 曲线完全不变。
- 开启后基础通道值不被直接改写。
- 连续 20 个事件无 NaN / Inf / 越界。
- 至少一个角色 demo 退出码 0。

详细设计见 [`META_EMOTION.md`](META_EMOTION.md)。

不新增情绪通道；不接 JEV / LLM 生成元情绪；不改 tolerance 参数；不在 L1/L2 提前实现。

## L4：关系域记忆

1. `memory_bias()` 接入 `Appraisal`，上限 ±0.2。
2. 关系摘要自动更新。
3. 写入闸门 v2：去重、冲突、时效、置信度。
4. 旧 `MemoryStoreDB` 迁移或退役。
5. prompt 预算压测 ≤1200 字。
6. 有记忆 / 无记忆消融实验。

验收：记忆只影响 appraisal / relationship / prompt，不写 `Personality`；100 轮后 prompt 仍 ≤1200 字。

## L5：忍耐空间

1. 用 golden set 校准 load / capacity / deadband / recovery。
2. 评估多维容量（愤怒 / 恐惧 / 亲密 / 批评）。
3. 伤疤按事件类型压缩容量。
4. 仪表盘 tolerance 区间带、load / capacity 折线。
5. 检查 hyper / hypo 表达提示与行为一致。

验收：低负荷永远 regulated；hypo 只在持续过载 + 退缩型占优时出现；两 tick 滞回不抖动。

## L6：表达 / LLM / JEV / MCP

1. JEV：开放文本 → 六维 `Score` → `Appraisal` + confidence + evidence。
2. 低置信度 / 冲突回退规则或 LLM；固定模型版本。
3. `Noul` 检测边界侵犯。
4. LLM 只表达，不把输出写回通道。
5. MCP：`tick` / `recall` / `state`。
6. 仪表盘展示 `StepTrace`，可回放一次事件。
7. JEV 成本护栏：缓存、批量、超时、fallback。

验收：JEV 不可用时全链路仍可运行；LLM 不改变 Personality 或核心公式；MCP 可被 Claude Code / Codex 直接调用。

## 顺序

```text
L0 → L1 → L2 → L3 → L3.5 → L6
                         ├→ L4 → L6
                         └→ L5 → L6
```

L3.5 依赖 L2（guilt / trust / love）与 L3（稳定的氛围/伤疤）；L4 / L5 可在 L3.5 后并行；L6 最后。

## 每层 Definition of Done

1. 代码 + 独立测试；
2. 有 `StepTrace` 或前后对照证据；
3. 所有旧测试通过；
4. 至少一个 demo / 场景退出码 0；
5. 更新 `docs/`、README、ROADMAP、CLAUDE.md；
6. 用户明确要求时才 commit / push。
