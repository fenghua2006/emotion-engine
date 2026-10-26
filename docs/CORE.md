# L0 核心情绪线

> 目标：先把**不依赖记忆、耐受、伤疤、氛围、LLM**的那部分情绪动力学固定下来。  
> 文件名：`core.py`；回归测试：`test_core_line.py`。

## 1. 为什么先做 L0

此前的 `engine.py` 是一个约 1,700 行的组合根：通道公式、记忆、耐受窗、氛围、伤疤、LLM 表达全在一个文件里。  
每加一层，都很难判断一条曲线变化到底来自哪一层。

L0 不新增情绪机制，只做三件事：

1. 把纯动力学抽到 `core.py`；
2. 用 `StepTrace` 暴露一次 tick 的中间状态；
3. 用行为保持测试锁住现有公式，防止后续层悄悄改坏底层曲线。

```text
L0 core.py         纯公式：通道 / 衰减 / 评估 / 门控 / 交互 / 慢通道
       │
       ├── L1 六条快通道：joy sadness anger fear disgust surprise
       ├── L2 慢通道与缺失通道：trust love longing guilt
       ├── L3 氛围与伤疤
       ├── L3.5 元情绪：二阶评估 / 调节意图 / 有界偏置（见 META_EMOTION.md）
       ├── L4 关系域记忆
       ├── L5 耐受校准：四域容量 / 伤疤定向 / meta load 可选消费
       └── L6 表达 / LLM / JEV / MCP / 仪表盘
```

元情绪不是 `core.py` 的新通道，而是读取 `StepTrace` 的上层。关闭时基础曲线必须完全不变。

已有功能不删除。增量开发的含义是：**逐层补契约、补回归、再改行为**，而不是重写一遍。

## 2. 模块边界

### `core.py` 可以依赖

- Python 标准库：`math`、`time`、`dataclasses`、`enum`、`typing`

### `core.py` 不可以依赖

- `engine.py`
- `atmosphere.py`
- `tolerance.py`
- `memory.py`
- `dashboard/`
- 任何 LLM / 网络 / SQLite

`atmosphere.py` 只依赖 `core.Channel` 与标准库，不反向依赖 `engine.py`。
`tolerance.py` 只依赖标准库，不导入 `engine` / `meta_emotion` / `core` /
`memory` / SQLite / 网络；由 `test_l5_tolerance.py` 的 AST 测试锁定。
`meta_emotion.py` 只依赖 `core` 与标准库（不导入 `engine` / `tolerance` /
`memory` / `atmosphere` / SQLite / 网络库），由 AST 测试锁定。

### 上层规则

- `engine.py` 负责组合，不重新定义核心公式。
- L1–L6（含 L3.5 元情绪）读取 `StepTrace` 或核心函数，提供**有界偏置 / 控制系数**，不改写 `decay()`、`appraise()`、`gate_appraisal()` 等公式。
- 任何会改变曲线的新参数，都必须有单独开关和前后对比测试；默认走向必须写进文档，不能静默改变。
- 行为修复保留 legacy 开关与旧 golden，确保修复前后可逐位对照。

## 3. 核心内容

| 对象 | 作用 |
|---|---|
| `Channel` | 10 通道枚举 |
| `FAST_CHANNELS` | joy / sadness / anger / fear / disgust / surprise / guilt |
| `SLOW_CHANNELS` | love / trust |
| `ABSENCE_CHANNELS` | longing |
| `NEGATIVE_CHANNELS` | sadness / anger / fear / disgust / guilt |
| `EmotionalState` | 原始通道值、`felt()` 感知压缩、`delta()` 帧间差 |
| `decay()` | 弹性衰减：偏离基线越远，等效半衰期越短 |
| `return_to_baseline()` | 每 tick 最多 30% 的轻拉力 |
| `saturate()` | 线性自然饱和，替代硬上限 |
| `trust_target()` | trust 随 love 上升的引力线 |
| `interaction_baselines()` | 慢通道交互基线：trust 用 `trust_target(love)`，love / 快通道 / guilt 用人格基线 |
| `grow_slow_channels()` | trust 向目标靠近；love 受信任缺口侵蚀或健康生长；正面事件按本区间增量输入 |
| `damage_trust()` | 背叛腰斩 trust，严重时二级削弱 love |
| `gate_appraisal()` | trust / love 门控，以及低信任越界惩罚 |
| `Appraisal` | 六维认知评估 |
| `appraise()` | 六维 → 通道原始激活；guilt 先过 `self_agency > 0.3` 门槛 |
| `apply_interactions()` | 通道交互矩阵；可传 `baseline` 使用基线相对激活，不传则保留旧绝对阈值行为；trust 正向增益可受 `trust_ceiling` 累计封顶 |
| `Personality` | OCEAN → 通道基线；人格锁定 |
| `StepTrace` | 一次 tick 的分阶段快照 |

## 4. StepTrace

`EmotionEngine.tick(..., trace=True)` 会额外返回 `result["trace"]`。  
`trace=False` 时结果与旧版完全一致。

```python
result = engine.tick(appraisal, now=now, trace=True)
trace = result["trace"]
```

记录阶段：

| 字段 | 含义 |
|---|---|
| `before` | 进入 tick、NaN 防护后的状态 |
| `after_decay` | 快通道衰减 + 基线回弹后 |
| `after_slow_channels` | trust / love 生长后 |
| `raw_appraisal` | `appraise()` 的原始激活 |
| `gated_appraisal` | 经 trust/love 门控、耐受增益、伤疤放大后的激活 |
| `after_appraisal` | 新事件写入通道后 |
| `after_atmosphere` | 氛围更新与越界 trust 惩罚后 |
| `after_interaction` | 交互矩阵后 |
| `final` | 本 tick 全部层执行完 |
| `blends` / `shock_channels` | 混合标签 / 冲击通道 |
| `slow_positive_input` | 本 tick 喂给 love 的正面事件增量（当前 0/1；legacy 为累计值） |
| `trust_target` | 交互阶段使用的 `trust_target(love)` 关系均衡线 |
| `atmosphere` / `tolerance_zone` | 本 tick 结束时的控制层状态 |
| `notes` | 顺序模式、时钟回拨等诊断信息 |
| `atmosphere_mode` | `corrected` / `legacy` |
| `atmosphere_before` | tick 入口氛围（corrected 已 clamp） |
| `atmosphere_applied` | 本 tick 实际用于 gated 的氛围 |
| `atmosphere_after` | tick 结束存储的氛围 |
| `atmosphere_shock_density` | 本 tick 漂移使用的 shock 窗口密度 |
| `atmosphere_arousal` | 目标使用的 `(fear + sadness) / 2` |
| `atmosphere_multipliers` | 氛围对各通道的实际乘数 |
| `scar_shift` | 实际生效的伤疤 shift（0 ~ 0.6） |
| `scar_channels` | 被伤疤放大的通道 |
| `gated_written` | 真正写入 state 的 gated |
| `meta_enabled` | 本次 trace 是否计算了元情绪（仅开启时出现） |
| `meta_appraisal` | 对 primary emotion 的四维元评估（仅开启时出现） |
| `meta_emotions` | 本 tick 激活的元情绪列表（仅开启时出现） |
| `regulation_bias` | 表达 / 调节 / 耐受的有界偏置（仅开启时出现） |

L3 字段只观察，不参与计算。L3.5 的四个 meta 字段同样只观察：
`meta_emotion_enabled=False` 时 trace 仍只有原来的 27 个键；
`meta_emotion_enabled=True` 且 `trace=True` 时才追加四个键。
`trace=False` 时不计算元情绪，结果与关闭时逐值相同。

L3 相位顺序：

- corrected：`after_appraisal`（含氛围调制）→ `after_interaction` → shock →
  氛围漂移 → `after_atmosphere` → `final`；
- legacy：`after_appraisal` → `after_atmosphere` → `after_interaction` → shock → `final`。

`StepTrace` 只观察，不参与计算；`mark()` 会复制状态，后续修改不会污染快照。

## 5. 行为保持

抽取后的验证：

```bash
python -m unittest test_core_line.py test_tolerance.py \
  test_engine_tolerance.py test_memory_system.py \
  test_l1_fast_channels.py test_interaction_activation.py \
  test_l2_slow_channels.py test_l3_atmosphere_scars.py \
  test_l3_5_meta_emotion.py test_l5_tolerance.py -v
# 229 tests, OK（含 32 个 L3、25 个 L3.5、38 个 L5 测试）
```

另外验证了：

```bash
python engine.py
python test_iloveyou.py
python characters/furina.py
python characters/kokomi.py
python characters/columbina.py
python characters/meta_emotion_demo.py
```

全部退出码 0。`test_trace_does_not_change_engine_result` 直接比较 `trace=False` 与 `trace=True` 的完整输出和最终状态。

## 6. L0 发现的两个顺序问题（L3 已修）

L0 刻意不修，以保持行为不变；L3 默认 corrected 修正两条，`legacy_atmosphere=True`
逐字保留旧路径与旧 notes。完整契约见 [`L3_ATMOSPHERE_SCARS.md`](L3_ATMOSPHERE_SCARS.md)。

### 6.1 氛围门控写在状态更新之后 → corrected 前移

旧路径先写通道，再用 atmosphere 修改 `gated`；调制只影响输出/快照，不改变本 tick 通道。

corrected：

```text
appraise → gate → tolerance gain → scar → atmosphere modulation → state write
```

tick t 的 shock 更新氛围，从 tick t+1 开始调制 gated；避免同 tick 循环依赖。
`gated_written` 直接暴露真正写入 state 的值。

legacy：保留晚调制，`gated_appraisal` 仍是晚调制后的输出，二者差异可见。

### 6.2 shock 密度在 shock 检测之前采样 → corrected 后移

旧路径在 shock 检测前计算 `len(shock_channels) / len(FAST_CHANNELS)`，密度恒为 0。

corrected：先检测 shock，追加真实强度到 `_recent_shocks`（window 5），
再计算密度并漂移氛围；氛围 clamp 到 `[-1, 1]`，单 tick 漂移 ≤0.1。

legacy：仍在检测前追加 0.0，不 clamp，旧行为精确保留。

### 6.3 L1.1 已修：交互矩阵把基线当成激活

这条不属于 L0 的两个顺序问题，而是交互触发语义的问题，已在 L1.1 修正。

旧 `apply_interactions()` 用绝对通道值 `< 0.05` 判断规则是否触发。surprise 基线
0.10、disgust 基线 0.06 都高于门槛，于是空闲 tick 会持续抬高 joy / fear / anger；
正面事件也会被 surprise→fear 加上无来源的 fear。

修正后：

- 传入 `baseline` 时，源激活 = `max(0, value - baseline)`；
- 快通道放大规则要求目标共激活，抑制规则不要求；
- `EmotionEngine` 默认使用 `Personality.baseline()`；
- `legacy_interactions=True` 保留旧行为。

对照数据与完整说明见 [`L1_FAST_CHANNELS.md`](L1_FAST_CHANNELS.md) §5。

## 7. L2 慢通道契约

L2 没有新增基础通道，只把 trust / love / longing / guilt 的语义固定下来。

### 7.1 交互基线

| 通道 | 基线 |
|---|---|
| trust | `trust_target(love)`（关系均衡线） |
| love | `Personality.baseline()[LOVE]` |
| 六快通道 + guilt | `Personality.baseline()`（guilt 基线为 0） |

原因：trust 是关系积分器。若用固定人格基线，正常关系里长到目标的 trust
会被交互矩阵当成“额外信任”，空闲 tick 持续压 fear。`gate_appraisal()`
里 trust 对 fear / anger / disgust 的绝对缓冲保留，交互矩阵只处理
“高于关系均衡线”的部分。

### 7.2 guilt 门槛

```text
self_agency = 1 - other_agency
self_agency ≤ 0.3  → guilt = 0
self_agency > 0.3  → (self_agency - 0.3) / 0.7 平滑放大
```

低于门槛不是“弱愧疚”，而是“不是我的错”。`legacy_slow_channels=True`
传门槛 0.0，可复现旧行为。

### 7.3 正面事件输入

`grow_slow_channels(..., positive_count, ...)` 的 `positive_count` 表示
**本区间新增**的正面事件数，不是历史累计。生产引擎每 tick 传 0/1；
legacy 模式传累计 `positive_events`，保留旧曲线。

### 7.4 数值护栏

- `trust ∈ [0, 1]`，`love ∈ [0, 2]`；
- `love → trust` 与 `guilt → trust` 的累计增量不超过 `trust_target(love)`；
- 抑制规则的幅度按源激活（高于基线的部分）计算，不用绝对通道值。

### 7.5 参考线与 golden

`slowline.py` + `l2_events.py` + 双 golden 锁定当前与 legacy 行为；
`test_l2_slow_channels.py` 有 32 个测试。完整决策与数据见
[`L2_SLOW_CHANNELS.md`](L2_SLOW_CHANNELS.md)。

## 8. L0 验收标准

- [x] `core.py` 不导入 `engine` / `tolerance` / `memory` / 网络 / SQLite。
- [x] `engine.py` 从 `core.py` 重新导出原有类型，旧 import 路径不变。
- [x] `tick(trace=True)` 返回分阶段快照，`trace=False` 行为不变。
- [x] 旧 41 个测试全部通过。
- [x] 新增 28 个核心测试，覆盖公式、门控、慢通道、trace 与兼容性。
- [x] 所有 demo 退出码 0。
- [x] L1 开始前，为六条快通道建立固定事件序列 golden set（见 [`L1_FAST_CHANNELS.md`](L1_FAST_CHANNELS.md)）。
