# 增量开发计划（L0–L7 + L3.5）

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
| **L3** | 氛围与伤疤 | ✅ 2026-10-26 | 顺序修正可 A/B；shock 密度非零；伤疤三通道 ≤60%；166 tests |
| **L3.5** | 元情绪：二阶评估与调节意图 | ✅ 2026-10-26（v0 trace-only） | 不新增通道；关闭时四套 golden 与 L3 A/B 不变；191 tests |
| **L4** | 关系域记忆 | ✅ 2026-10-27（v0.2） | 先验只进 Appraisal；摘要 / 闸门 / 迁移；285 tests |
| **L5** | 忍耐空间 / 耐受校准 | ✅ 2026-10-26（校准核心 v0 + L5.1） | 双 golden；四域容量；L4 后 285 tests |
| **P0** | 可维护性护栏 | ✅ 2026-10-27 | tick 拆分 / 表达&LLM 分层 / ruff+coverage+CI / schema 版本；285 tests |
| **L6** | 表达 / LLM / JEV / MCP | ✅ 2026-10-27（v0） | 规则优先 + LLM 可选回退；Noul 边界信号；MCP stdio；338 tests |
| **L7** | 多角色情绪感染 | ✅ 2026-10-27（v0 + v0.1 + v0.2） | 单向 A→B + 双向 A↔B；四维有界先验；显式 / 自动关系强度；默认路径零变化；393 tests |

## L0：核心情绪线 ✅

交付：

- `core.py`：通道、`EmotionalState`、衰减、回弹、饱和、慢通道、六维评估、门控、交互矩阵。
- `StepTrace`：`before → after_decay → after_slow_channels → after_appraisal → after_interaction → after_atmosphere → final`（L3 corrected；legacy 保留 `after_appraisal → after_atmosphere → after_interaction` 旧序）。
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

### 已知顺序问题（L3 已修，legacy 保留）

1. 氛围门控原先在 state 写入之后才修改 `gated`，本 tick 实际不生效。
2. shock 密度原先在 shock 检测之前采样，`_recent_shocks` 长期为 0。

L3 默认 corrected 已修正两条；`legacy_atmosphere=True` 逐字保留旧顺序与旧 notes，
并锁定 pre-L3 单事件精确值。详见 [`L3_ATMOSPHERE_SCARS.md`](L3_ATMOSPHERE_SCARS.md)。

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

## L3：氛围与伤疤 ✅

目标：修正 pre-L3 的两处组合根顺序问题，让 atmosphere 真正作用于当前 tick；
锁住 shock 采样、氛围边界与伤疤放大范围。L1/L2 参考线和四套 golden 不变。

交付：

- `atmosphere.py`：纯函数模块，负责 clamp、目标氛围、单步漂移、氛围调制、
  伤疤 shift 上限与三通道放大；不导入 engine / tolerance / memory / SQLite / 网络。
- `engine.py`：默认 corrected 顺序；`legacy_atmosphere=True` 保留 pre-L3 行为；
  `wake()`、`tick()` 入口与快照加载都执行 corrected clamp。
- `core.py`：`StepTrace` 增加 `atmosphere_mode / before / applied / after /
  shock_density / arousal / multipliers`、`scar_shift / scar_channels`、
  `gated_written`，只观察不参与计算。
- `test_l3_atmosphere_scars.py`：32 个测试。
- `docs/L3_ATMOSPHERE_SCARS.md`：公式、顺序、A/B 数据、限制与“不设 L3 golden”决策。

corrected 顺序：

```text
entry clamp → decay → slow growth → appraise → gate → tolerance gain
→ scar amplification → atmosphere modulation → state write
→ boundary penalty → interactions → shock detection
→ shock density → atmosphere drift (next tick) → memory / Saga → tolerance
```

关键口径：

- tick t 的氛围调制 tick t 的 gated；tick t 的 shock 更新 tick t+1 的氛围。
- shock 密度分母仍为 `len(FAST_CHANNELS)` = 7（含 guilt）。
- 伤疤只放大 sadness / fear / anger；effective shift ∈ `[0, 0.6]`，乘数 ≤1.6。
- legacy：state 写入后漂移（空 shock、不 clamp）、晚调制 gated、两条旧 notes。

验收结果：

```text
32 L3 tests, OK
166 tests total, OK
四套 golden 全部通过：L1 current / L1 legacy / L2 current / L2 legacy
单次严重事件 corrected atmosphere -0.200000，shock density 0.714300（5/7）
legacy 同事件逐值保留：atmosphere -0.3774803783593781，
  sadness 0.6125356194066094，anger 0.5079335958919840，fear 0.6160595893163425
紧绷 +0.6 vs 轻松 -0.6：sadness 0.729544 vs 0.524779
```

不设 L3 reference line / JSON golden：L3 是组合根顺序与控制层修正，不是新的纯通道曲线；
做 L3Line 会复制 tolerance、memory、Saga、scar store。契约改由纯函数测试、engine A/B、
一条 pre-L3 精确兼容值，以及 L1/L2 四套 golden 不变共同锁定。

详细记录见 [`L3_ATMOSPHERE_SCARS.md`](L3_ATMOSPHERE_SCARS.md)。

## L3.5：元情绪 ✅ v0（trace-only）

目标：让引擎能回答“角色对这个情绪本身是什么态度”，而不是只回答“角色有多愤怒”。

核心约束：

- 元情绪是**二阶过程**，不是第 11 个基础通道。
- `target` 指向一个 primary channel；`kind` 是 shame / guilt / pride / contempt / compassion / interest / fear 等标签。
- 元评估四维：`goal_conduciveness`、`social_acceptability`、`controllability`、`identity_congruence`。
- 输出只做有界偏置：表达、调节意图、耐受负荷；不改写 `core.py` 公式，不写 `Personality`。
- 角色稳定倾向放 `MetaEmotionProfile`，瞬时状态放 `MetaEmotionState`。
- **v0 只写 trace**：`regulation_bias` 不进入 `respond()`、tolerance 或下一 tick appraisal。

交付：

- `meta_emotion.py`：纯函数层，五个 frozen dataclass；双阈值激活
  （`felt_diff ≥ 0.04`、`raw_excess ≥ 0.03`）；四维公式；七个 kind、六个 action；
  `meta_appraise()` / `meta_regulate()` / `evaluate_meta_emotion()`。
- `core.py`：`StepTrace` 增加 `meta_enabled / meta_appraisal / meta_emotions /
  regulation_bias`，仅开启时序列化。
- `engine.py`：`meta_emotion_enabled=False`、`meta_emotion_profile=None`；
  `trace=False` 时完全不计算。
- `characters/furina.py`：`furina_meta_shame_profile` / `furina_meta_pride_profile`；
  `create_furina()` 现有无参调用保持兼容。
- `characters/meta_emotion_demo.py`：同一 anger、同一状态、同一时钟，
  shame profile → `shame 0.585 / suppress`，pride profile → `pride 0.340 / express`。
- `test_l3_5_meta_emotion.py`：25 个测试，含 AST 模块边界与 snapshot 不持久化。
- `docs/META_EMOTION.md`：激活规则、公式、profile schema、trace 形状、验收结果。

边界：

- 不新增情绪通道；不接 JEV / LLM 生成元情绪；不改 tolerance 参数；
  不持久化 meta state / profile / enable 开关；不新增 golden，不重新生成 fixtures。
- L3.5 当时 `tolerance_load_bias`、`expression_gain`、`display_rule` 均为观测值；
  L5 已为 tolerance load 新增默认关闭的开关；表达侧（`expression_gain` /
  `display_rule`）仍未接入。

验收结果：

```text
25 L3.5 tests, OK
191 tests total, OK
四套 golden 全部通过：L1 current / L1 legacy / L2 current / L2 legacy
meta 关闭 trace 仍为原 27 键；开启只增加 4 键
meta 开启 + trace=False 与关闭逐值相同
L3 corrected：atmosphere -0.200000，shock density 0.714300
L3 legacy：atmosphere -0.3774803783593781，sadness 0.6125356194066094，
  anger 0.5079335958919840，fear 0.6160595893163425
所有角色 demo 与 meta demo 退出 0，fixtures/ 无 diff
```

详细设计见 [`META_EMOTION.md`](META_EMOTION.md)。

## L4：关系域记忆 ✅ v0.2

交付：

- `core.py`：`apply_appraisal_bias()` 只改六维 `Appraisal`；
  每维先按 `cap=0.2` 截断，再按合法范围 clamp；`Appraisal.to_dict()`。
- `engine.py`：
  - `tick(appraisal_bias=...)` 在 `appraise()` 前叠加先验；
  - 没有 `Appraisal` 时不拉状态；
  - 默认 trace 键集合不变；只在先验存在时追加
    `memory_bias_enabled / memory_bias_applied / appraisal_before_memory`；
  - `respond()` / `respond_llm()` 支持显式 `appraisal` 与记忆先验透传；
  - 回复后写记忆时携带 `appraisal` 快照。
- `memory.py`：
  - `appraisal_prior()`：卡片六维评估减去中性基线后加权平均，±0.2；
  - `memory_prior()`：同时返回六维与十通道先验；
  - `MemoryWriteGate`：置信度、去重、冲突、开放循环去重、轮次预算；
  - `compose_relationship_summary()` / `update_relationship_summary()`：
    确定性摘要，每次成功写入后自动刷新；
  - `migrate_legacy_memories()`：旧 `emotion.db` → 关系域记忆，可重复执行。
- `scripts/migrate_legacy_memory.py`：迁移 CLI，支持 `--dry-run`。
- `test_l4_memory_integration.py`：27 个测试。

验收结果：

```text
appraisal prior signed / capped ±0.2 / user isolated
有先验 vs 无先验：state 改变，Personality 逐值不变
无 Appraisal 时先验不拉状态
默认 trace 无 memory 键；有先验只多 3 键
100 轮后 prompt ≤1200 字；有/无记忆消融通过
摘要自动刷新；闸门去重 / supersede / 轮次预算通过
旧库迁移二次执行 migrated=0，skipped_duplicate=1
全套 285 tests OK；L1/L2/L5 六套 golden 无 diff
```

仍未做：

- 文本 → `Appraisal` 的 JEV 已由 L6 补齐（`jev.py`，默认关闭、可回退）；
  记忆先验仍需显式 `Appraisal` 才能生效。
- 摘要的 LLM 叙事版；规则版只压缩结构化信息。
- 旧 `MemoryStoreDB` 的自动退役；目前只提供迁移工具。

## L5：耐受校准 ✅ v0（校准核心）

目标：把 `tolerance.py` 从手调标量控制层升级为可回归、可 A/B 的校准层，
同时保持旧行为可逐值回退。

交付：

- `tolerance.py`：
  - 默认 `capacity_model="multi"`，输出 `anger / fear / intimacy / criticism`
    四域容量，有效 capacity 为加权平均；
  - `SCAR_DOMAIN_MAP` 按事件类型压缩对应域，同域取 max；
  - `meta_load_cap` / `domain_scar_load` 新配置；
  - `capacity_by_domain` / `meta_load_bias` 观测字段；
  - multi 序列化新三键，scalar 仍只输出旧六键。
- `engine.py`：
  - `tolerance_config`、`legacy_tolerance=False`、
    `tolerance_meta_load_enabled=False`；
  - `_scar_pressure_by_tag()`；
  - 开启 meta load 时，在 Step 8 前用上一 tick zone / overload 计算一次；
  - 快照保存 `legacy_tolerance` 与 multi 观测字段；配置不持久化。
- `l5_events.py`：15 个冻结场景 / 41 步；`l5line.py` 提供
  `run_scenario` / `run_sequence` 共享 current / legacy runner。
- `scripts/generate_l5_tolerance_golden.py`：`--check` / `--legacy`。
- `fixtures/l5_tolerance_golden.json` / `..._legacy.json`。
- `test_l5_tolerance.py`：38 个测试。
- `docs/TOLERANCE.md`：模型、系数、伤疤映射、meta bias、golden、限制。

模型要点：

1. 四域系数加权平均对齐旧标量目标（trust `.28`、love `.1375`、
   neuro `.205`、atm+ `.102`、atm− `.0495`），中性漂移 `<0.01`。
2. 每域先 clamp 到 `[0.35, 1.80]`，再加权；极端输入不保证有效容量触底。
3. 伤疤：`social_hurt` → anger/fear/criticism；`criticism` → anger/criticism；
   `cold_shoulder` → fear/intimacy；`self_blame` → intimacy。
4. meta bias 默认关闭；开启后加在 instant load，硬上限 `0.20`，不逐 tick 累加。
5. `legacy_tolerance=True` 强制 scalar + global max scar + 忽略 meta bias。

验收结果：

```text
38 L5 tests, OK；L5.1 追加 12 tests；L4 后全套 285 tests, OK
两套 golden 通过；L1/L2 四套 golden 无 diff
calm current capacity 0.862350；legacy 0.857500
anger_hyper: 1 tick pending → 2 tick hyper
withdrawal_hypo: 2 tick hypo
recovery: hyper → 4h → pending → regulated
scar social_hurt 0.6: anger/fear/criticism −0.12，intimacy 不变
capacity floor 0.35 / ceiling 1.80
meta bias 0.05/0.20/0.50 → +0.05/+0.20/+0.20
fuzz 500 组：有限、[0.35,1.80]、[0,3]、合法 zone
```

L5.1 ✅ 已补（表达 / 仪表盘）：

- 仪表盘 tolerance 区间带、load / capacity / 四域折线；
- hyper / hypo 本地表达模板、Live2D 与 `_action_tendency` 一致性；
- `regulated` 的表情映射保持逐值不变，新修正只作用于 hyper / hypo；
- 12 个 L5.1 测试；L4 后全量 285 tests OK。

仍未做：

- 长期压力导致的基线容量慢漂移；
- 伤疤跨域扩散；
- 真实 Live2D 观感 A/B 与更多 zone 台词。

详细记录见 [`TOLERANCE.md`](TOLERANCE.md)。

## P0：可维护性护栏 ✅

交付：

- `engine.py`：`tick()` 只做编排；实际阶段拆为 `_begin_tick` / `_step_decay` /
  `_step_slow_channels` / `_step_appraisal` / `_step_after_appraisal` /
  `_step_interactions` / `_step_shock` / `_step_memory_and_saga` /
  `_step_meta_and_tolerance` / `_build_tick_result`。
- `expression.py`：动作倾向、台词池、情绪快照、mood hint、LLM prompt、Live2D 参数；
  `engine.py` 继续 re-export 旧名字。
- `llm_client.py`：主 API → fallback → `(None, None)`；不读取引擎状态。
- 快照：`SNAPSHOT_VERSION`（当前 2）+ `_migrate_snapshot_payload()`；
  直接通道字典 / 无版本 payload 迁移到 v2，未来版本拒绝加载。
- 记忆：`MEMORY_SCHEMA_VERSION`（当前 1）+ `PRAGMA user_version` +
  `_migrate_schema()`；旧库 0 → 1，未来版本抛 `RuntimeError`。
- `pyproject.toml`：ruff `E`/`F`（忽略 `E501`）、coverage branch、gate 80%。
- `.github/workflows/ci.yml`：ruff → unittest → coverage → 六套 golden。
- `test_p0_maintainability.py`：17 个测试（表达层边界、LLM 无 key/fallback、
  tick 顺序、trace 阶段、快照版本、记忆 schema 版本）。

验收：

```text
285 tests OK
ruff check . -> All checks passed
coverage total 86%（branch，核心模块；gate 80%）
L1/L2/L5 六套 golden 无 diff
```

详细分层、tick 顺序和修改检查表见 `docs/ARCHITECTURE.md`。

## L6：表达 / LLM / JEV / MCP ✅

交付：

- `jev.py`：开放文本 → 六维 `Appraisal` + confidence + evidence。
  规则优先，低置信度（默认 `<0.45`）或显式 `force_llm=True` 时才可选 LLM；
  `JEVResult` 携带 `source`（rules / llm / fallback）与 `cached`。
- 成本护栏：有界 LRU（默认 256）、`analyze_many` 按 `max_batch=64` 分块、
  8s 超时、异常 / 无 key / JSON 失败全部回退规则结果；输出统一 clamp。
- `noul.py`：亲密 / 命令 / 侮辱 / 胁迫 / 威胁五类文本边界信号；
  亲密权重随 `trust` 变化；只读文本，不写状态或记忆。
- `engine.respond_llm(..., use_jev=False, jev=None, auto_boundary=False,
  boundary_violation=None, trace=False)` 与 `respond(..., boundary_violation,
  trace)`；新参数默认关闭，旧调用逐位不变；失败写 `_jev_error` /
  `_boundary_error`，主链路继续。
- LLM 只进入 `utterance`，不写回通道、`Personality` 或核心公式。
- `mcp_server.py`：零依赖 stdio JSON-RPC，`tick` / `recall` / `state`；
  记忆懒加载并按 `(character_id, user_id)` 隔离；可直接被 Claude Code /
  Codex 调用。
- `scripts/replay_trace.py` + 仪表盘 `POST /api/replay`：用只读副本回放一轮，
  不改真实引擎 / 快照 / 记忆。
- 测试：`test_l6_jev.py`（22）、`test_l6_integration.py`（15）、
  `test_l6_mcp.py`（16）；文档 `docs/JEV.md` / `docs/MCP.md`。

验收结果：

```text
338 tests OK
ruff check . -> All checks passed
JEV 无 key / 超时 / 异常时全链路仍可运行
LLM 前后 Personality 逐值不变
MCP initialize / tools/list / tools/call / stdio 子进程往返通过
L1/L2/L5 六套 golden 无 diff
```

仍未做：

- 反讽、长距离指代与复杂否定的语义评估；
- JEV 规则词典的系统化扩充与标注集评估；
- 表达侧消费 L3.5 的 `expression_gain` / `display_rule`；
- 群体情绪感染与角色关系网络（L7.1 已补双向与关系强度，L7.2 已补自动关系模型）。

## L7：多角色情绪感染 ✅ v0 + v0.1 双向 + v0.2 自动关系

目标：让 A 的 `Appraisal` 成为 B 的有界输入，同时保持两个角色的状态、记忆、
人格与耐受完全隔离。v0 做单向 A → B；v0.1 增加双向 A ↔ B 与关系强度；
v0.2 从 trust / love 自动推导关系强度。

交付：

- `contagion.py`：
  - `CONTAGION_DIMENSIONS` 固定四维：`goal_conduciveness` /
    `expectedness` / `coping_potential` / `social_evaluation`；
  - `derive_contagion_bias()`：按中性值归一化，乘 `gain`（默认 0.2），
    单维截断到 `±cap`（默认 0.08，绝对上限 0.2）；
  - `sanitize_contagion_bias()`：只接受 Mapping，过滤 bool / 非数值 /
    NaN / Inf / 未知 key，逐维 clamp，空结果返回 `None`；
  - `ContagionPair`：`tick_pair()` 先 A 后 B，`tick_target()` 只 tick B；
    `tick_bidirectional()` 先算双方输入偏置再 tick，顺序无关、无同 tick
    反馈环；`strength` 乘在 `gain` 上（默认 1.0，限制在 `[0, 1]`）；
    拒绝同一引擎或共享 `state` / `memory` / `scars` / `tolerance`，
    共享只读 `Personality` 允许；
  - `relationship_strength()`：trust / love 加权（默认 0.6 / 0.4，love 按
    [0, 2] 归一化），非法值按 0；
  - `ContagionPair.from_relationship()` / `auto_strength=True`：双方强度取
    平均，并在每次 `derive()` 前刷新；仍受 `cap` 限制；
  - 不导入 `engine.py`，通过 duck typing 调用 `.tick()`。
- `core.py`：`StepTrace` 增加 `contagion_enabled` / `contagion_applied` /
  `appraisal_before_contagion`，仅感染实际生效且 `trace=True` 时序列化。
- `engine.py`：
  - `tick(..., contagion_bias=None)` 在 L4 记忆先验之后、`appraise()` 之前
    叠加；
  - 无 `Appraisal` 时完全 no-op；
  - 默认路径 result / trace 键集合不变；感染生效时 result 只多
    `contagion_bias`，trace 只多三个键；
  - `respond()` / `respond_llm()` 透传可选 `contagion_bias`。
- `scripts/multi_character_demo.py`：A 经历负面事件，B 在有无感染下对比；
  `--bidirectional` 展示双向，`--strength` 展示显式关系强度，
  `--auto-strength` 展示 trust / love 自动推导。
- `test_l7_contagion.py`（14）+ `test_l7_integration.py`（13）+
  `test_l7_1_bidirectional.py`（14）+ `test_l7_2_relationship_model.py`（14）。
- `docs/L7_MULTI_CHARACTER.md`：数据流、公式、API、双向、自动关系、隔离、限制。

关键口径：

- 只传四维；`goal_relevance` / `other_agency` 不跨角色传输。
- 同一 tick 的慢通道在 appraisal 之前运行，因此 love / trust 生长仍看原始
  appraisal；感染只影响本 tick 评估与后续状态。
- 双向的两个偏置都来自输入 appraisal，不读取对方刚更新的情绪输出；
  跨轮反馈由调用方控制。
- 自动关系强度只读当前 trust / love，不读历史；只放大 gain，不绕过 cap。
- 感染不写状态、记忆、`Personality`、快照、MCP；schema 版本不变。
- 默认路径与六套 golden 逐位不变。

验收结果：

```text
55 L7 tests, OK
393 tests total, OK
ruff check . -> All checks passed
coverage report -> 87% total（branch，gate 80）
L1/L2/L5 六套 golden 无 diff
demo：单向 B 四维 bias -0.08；双向 A/B 各自获得对方偏置；
      strength=0.5 时 effective_gain=0.1；auto-strength 输出 mode=auto；
      A 不受单向感染的 B 影响
```

仍未做：

- 方向性关系模型（区分 A→B 与 B→A 的强度）；
- 群体传播网络、传播链与距离衰减；
- 用 A 的最终表达状态而不仅是输入 appraisal 作为感染源。

## 顺序

```text
L0 → L1 → L2 → L3 → L3.5 → L5 ✅ → L4 ✅ → P0 ✅ → L6 ✅ → L7 ✅
```

L3.5 依赖 L2（guilt / trust / love）与 L3（稳定的氛围/伤疤）；L5 校准核心与
L5.1 表达/仪表盘已完成；L4 v0.2、P0 可维护性护栏、L6（JEV / Noul / LLM /
MCP）与 L7 v0.2（单向 + 双向感染、显式 / 自动关系强度）已完成。下一层可按需
进入方向性关系模型 / 群体传播，或表达侧元情绪接入。

## 每层 Definition of Done

1. 代码 + 独立测试；
2. 有 `StepTrace` 或前后对照证据；
3. 所有旧测试通过；
4. 至少一个 demo / 场景退出码 0；
5. 更新 `docs/`、README、ROADMAP、CLAUDE.md；
6. 用户明确要求时才 commit / push。
