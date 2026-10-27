# L3 氛围与伤疤

> 目标：修正 pre-L3 的两处顺序问题，把 atmosphere 变成真正作用于当前 tick 的控制层，
> 并锁住伤疤只放大 sadness / fear / anger、上限 60%。  
> 状态：✅ 2026-10-26。默认 corrected；`legacy_atmosphere=True` 保留 pre-L3 曲线。  
> 代码：`atmosphere.py`、`engine.py`、`core.py`、`test_l3_atmosphere_scars.py`。  
> L1/L2 四套 golden 未改动。

## 1. 范围

L3 只处理组合根的氛围 / 伤疤顺序与边界：

包含：

- atmosphere 在 state 写入之前调制 `gated`；
- 先检测 shock，再用**非零** shock 密度更新 atmosphere；
- corrected 模式 atmosphere ∈ `[-1, 1]`；
- 伤疤只放大 sadness / fear / anger，effective shift ∈ `[0, 0.6]`；
- `legacy_atmosphere` 兼容开关、快照持久化；
- StepTrace 的 L3 观测字段；
- 无伤疤 / 有伤疤 A/B 测试。

不包含：

- 不新增情绪通道；
- 不改 `core.py` 的衰减、评估、门控、交互权重；
- 不改 tolerance 参数、memory、Saga、LLM、JEV；
- 不新增 L3 reference line 或 JSON golden（原因见 §7）。

## 2. pre-L3 问题

### 2.1 氛围调制写入过晚

旧 `tick()`：

```text
write gated → atmosphere drift → gated *= atmosphere factor
```

`gated` 已经写进 state，之后的乘法只影响输出/快照，不改变本 tick 的通道值。
氛围只能通过 tolerance capacity 间接影响下一 tick。

### 2.2 shock 密度恒为 0

旧 `tick()` 在 shock 检测之前执行：

```python
recent_shock_intensity = len(shock_channels) / len(FAST_CHANNELS)
```

此时 `shock_channels` 还是空列表，`_recent_shocks` 每 tick 记录 0，
`atmosphere_target` 的 shock 项从未生效。

## 3. corrected 顺序

```text
0. 入口 clamp atmosphere；记录 atmosphere_before
1. snapshot_previous
2. fast decay + longing decay
3. slow growth
4. appraise → gate_appraisal
5. tolerance negative_gain
6. scar amplification（≤ 60%，仅三通道）
7. atmosphere modulation（用 atmosphere_before）
8. state write（gated_written）
9. boundary trust penalty
10. interaction matrix
11. shock detection
12. 计算 shock density → atmosphere drift → clamp
13. memory / Saga
14. tolerance.update（看到本 tick 的新氛围与 shock）
15. final
```

两个关键点：

- **tick t 的氛围作用于 tick t 的 gated**。tick t 检测到的 shock 更新氛围，
  从 tick t+1 开始生效。这样既满足“先检测 shock，再更新氛围”，
  又避免同 tick 的循环依赖。
- 氛围目标中的 arousal 在 state 写入后、interactions 前捕获。
  因此 corrected 相对 legacy 的目标差异只来自非零 shock 密度，
  不额外混入交互后的通道变化。

### 3.1 公式

```text
target = -0.6 + shock_density * 1.2 + arousal * 0.4
         (+0.3 if boundary_violation)

arousal = (fear + sadness) / 2
shock_density = mean(_recent_shocks)      # window = 5
shock_intensity = len(shock_channels) / len(FAST_CHANNELS)

atmosphere_next = clamp(
    atmosphere + clamp(target - atmosphere, -0.1, +0.1),
    -1, +1,
)
```

`len(FAST_CHANNELS)` 为 7（含 guilt）；L3 不修改这个分母。

### 3.2 氛围调制

只影响 joy / sadness / fear / anger：

| atmosphere | joy | sadness / fear / anger |
|---|---|---|
| `> 0` 紧绷 | × `(1 - 0.3·atm)` | × `(1 + 0.4·atm)` |
| `< 0` 轻松 | × `(1 - 0.2·atm)` | × `(1 + 0.3·atm)` |
| `0` | 不变 | 不变 |

disgust / surprise / guilt / love / trust / longing 不受直接调制。

## 4. 伤疤

`SensitizationStore` 仍是状态实体，留在 `engine.py`；纯变换在 `atmosphere.py`。

约束：

- 只在 `goal_conducibility < 0` 时放大；
- 只放大 sadness / fear / anger；
- 通道值 `> 0.001`、effective shift `> 0.01` 才处理；
- `effective_shift = clamp(threshold_shift, 0, 0.6)`；
- 最大乘数 `1 + 0.6 = 1.6`；
- `register()` 单次仍降 8%，第 3 次开始，最大 60%；
- `get_shift()` 对损坏/恢复的超限值再 clamp 一次；
- `_scar_pressure()` 也使用 clamp 后的值。

伤疤上限在 corrected 与 legacy 两种模式下都生效。正常历史数据的 shift 本来就不会超过
0.6，因此该限制不改变旧曲线；它只硬化异常快照。

## 5. 兼容开关

```python
EmotionEngine(legacy_atmosphere=True)
```

legacy 路径保留：

- state 写入后、interactions 前漂移 atmosphere；
- 在 shock 检测前采样空的 `shock_channels`，`_recent_shocks` 恒记 0；
- mood 调制发生在 state 写入之后，只影响输出/快照；
- 不 clamp atmosphere；
- 两条旧 trace notes 逐字保留。

corrected 路径 notes：

```text
L3 corrected: shock detected before atmosphere update; density applies next tick
L3 corrected: atmosphere gate applied before state write
```

legacy 路径 notes：

```text
atmosphere gate mutates gated after state write (preserved L0 behavior)
shock density sampled before shock detection (preserved L0 behavior)
```

快照：

- `save_snapshot()` 持久化 `legacy_atmosphere`；
- `load_snapshot()` 先恢复 flag，再决定是否 clamp atmosphere；
- 旧快照没有该键时保留 engine 当前 flag（默认 corrected）；
- corrected 模式在 `tick()` 入口、加载快照、`wake()` 入口都会 clamp。

## 6. StepTrace

新增字段（只观察，不参与计算）：

| 字段 | 含义 |
|---|---|
| `atmosphere_mode` | `corrected` / `legacy` |
| `atmosphere_before` | tick 入口值（corrected 已 clamp） |
| `atmosphere_applied` | 实际用于 gated 的值 |
| `atmosphere_after` | tick 结束存储值 |
| `atmosphere_shock_density` | 本 tick 漂移使用的窗口密度 |
| `atmosphere_arousal` | 目标使用的 `(fear + sadness) / 2` |
| `atmosphere_multipliers` | 各通道实际乘数 |
| `scar_shift` | 实际生效的伤疤 shift（0 ~ 0.6） |
| `scar_channels` | 被放大的通道 |
| `gated_written` | 真正写入 state 的 gated |

相位顺序：

- corrected：`after_appraisal`（含氛围调制）→ `after_interaction` → shock → 氛围漂移 →
  `after_atmosphere` → `final`；
- legacy：`after_appraisal` → `after_atmosphere` → `after_interaction` → shock → `final`。

`gated_written` 在 corrected 下等于 `gated_appraisal`；legacy 下是晚调制前的值，
而 `gated_appraisal` 仍是旧版晚调制后的值，二者差异直接可见。

## 7. 测试与 A/B 数据

```bash
python -m unittest test_l3_atmosphere_scars.py -v
# 32 tests, OK

python -m unittest discover
# 166 tests, OK
```

四套 golden 均未改变：

```text
l1_golden.json
l1_golden_legacy.json
l2_golden.json
l2_golden_legacy.json
```

### 7.1 单次严重事件

默认人格，`now = _last_update + 60`，同一 severe appraisal：

| 指标 | corrected | legacy |
|---|---:|---:|
| atmosphere | -0.200000 | -0.377480 |
| shock density | 0.714300（5/7） | 0.000000 |
| sadness | 0.568657 | 0.612536 |
| anger | 0.473063 | 0.507934 |
| fear | 0.582154 | 0.616060 |

corrected 首 tick 的负面略低，因为初始 `-0.3` 的轻松氛围在写入前起了缓冲；
legacy 的 `-0.3` 是在写入后调制，未影响本 tick 通道。

### 7.2 紧绷 vs 轻松

同一 severe appraisal，手动播种 atmosphere：

| 指标 | `+0.6` 紧绷 | `-0.6` 轻松 |
|---|---:|---:|
| sadness | 0.729544 | 0.524779 |
| anger | 0.600921 | 0.438193 |
| fear | 0.706475 | 0.548248 |
| joy | 0.353388 | 0.354628 |

紧绷氛围显著提高负面反应，符合 L3 验收。

### 7.3 连续 12 次严重事件

| tick | corrected atmosphere | legacy atmosphere | corrected density | legacy density |
|---:|---:|---:|---:|---:|
| 1 | -0.200000 | -0.377480 | 0.714300 | 0.000000 |
| 3 | 0.000000 | -0.177480 | 0.666700 | 0.000000 |
| 5 | 0.200000 | 0.022520 | 0.628600 | 0.000000 |
| 8 | 0.500000 | 0.322520 | 0.314300 | 0.000000 |
| 12 | 0.505788 | 0.475436 | 0.000000 | 0.000000 |

corrected 的氛围更早变紧绷，且全程有界。

### 7.4 pre-L3 精确兼容

`legacy_atmosphere=True` 在 HEAD `9926289` 的单事件结果被逐值锁定：

```text
atmosphere  -0.3774803783593781
sadness      0.6125356194066094
anger        0.5079335958919840
fear         0.6160595893163425
shock        ["sadness", "anger", "fear", "disgust", "surprise"]
_recent_shocks == [0.0]
```

### 7.5 为什么不设 L3 golden

L1 / L2 golden 是纯参考线的通道曲线；L3 是组合根的顺序与控制层修正。
要做 L3Line，必须复制 tolerance、memory、Saga 和 scar store，形成第二个 engine，
维护面大且会掩盖真正的组合根语义。

L3 的契约用以下方式锁定：

- `atmosphere.py` 纯函数测试；
- engine corrected / legacy A/B；
- 一条 pre-L3 精确兼容值；
- L1/L2 四套 golden 不变。

L3.5 的验收措辞相应改为“关闭元情绪时 L3 A/B 场景不变”，而不是“L3 golden 不变”。

## 8. 已知限制

- atmosphere 的 shock 响应有一 tick 的**应用**延迟：本 tick 检测到的 shock 更新氛围，
  从下一 tick 才开始调制 gated。这是刻意避免同 tick 循环依赖。
- 首个 tick 仍使用初始 `-0.3`；单次严重事件不会立刻把氛围变成正值。
- shock 分母是 `len(FAST_CHANNELS)` = 7，包含 guilt；L3 未改。
- 下界 `-1.0` 在自然事件序列中很少触及：基础目标为 -0.6，arousal 只会抬高目标；
  下界主要防止异常快照/手动值。
- L3 的通道放大仍按单条伤疤 shift 生效；L5 起 tolerance capacity 默认按事件类型
  定向压缩，`legacy_tolerance=True` / `capacity_model="scalar"` 保留旧的 global max
  `scar_pressure` 语义。

## 9. 与 L3.5 的边界

L3.5 元情绪已完成（v0，trace-only）：

- 默认关闭；关闭时 L1/L2 四套 golden 与 L3 corrected / legacy A/B 场景逐值不变。
- 开启后只增加 `StepTrace` 的四个 meta 键，不改 atmosphere 顺序、漂移、
  clamp 或伤疤放大逻辑。
- 元情绪读取最终的 `atmosphere`、`scar_shift`、`scar_channels`，只做二阶评估；
  不回写氛围或伤疤。
- `tolerance_load_bias` 默认仍是观测值；L5 显式开启后才进入 tolerance。

L3.5 的实现与验收见 [`META_EMOTION.md`](META_EMOTION.md)。

## 9.5 与 L5 的边界

L5 校准核心已完成（2026-10-26）：

- `_scar_pressure_by_tag()` 把所有已登记 tag 的 clamp 后 `threshold_shift`
  交给 `ToleranceState`；L5 用 `SCAR_DOMAIN_MAP` 按域取 max。
- 通道放大逻辑（sadness / fear / anger，≤1.6×）不变；L5 只改 capacity 压缩。
- `legacy_tolerance=True` 或 `capacity_model="scalar"`：忽略 tag map，回到
  单条最大 `scar_pressure` 与旧 `scar_load`，逐值复现 pre-L5。
- 伤疤压缩 capacity、meta load 增加 load，两者不重复计同一来源。
- L5 的 golden 与验收见 [`TOLERANCE.md`](TOLERANCE.md)。

## 10. 相关文档

- [`CORE.md`](CORE.md) — L0 纯动力学与 StepTrace
- [`L1_FAST_CHANNELS.md`](L1_FAST_CHANNELS.md) — L1 快通道
- [`L2_SLOW_CHANNELS.md`](L2_SLOW_CHANNELS.md) — L2 慢通道
- [`TOLERANCE.md`](TOLERANCE.md) — 耐受窗
- [`META_EMOTION.md`](META_EMOTION.md) — L3.5 元情绪
- [`INCREMENTAL_PLAN.md`](INCREMENTAL_PLAN.md) — L0–L7 + L3.5 计划
