# L2 慢通道与缺失通道

> 目标：把 trust / love / longing / guilt 从“生产引擎里顺手长出来的量”变成
> 可独立验证的慢通道语义，并锁定 slow → fast 的基线口径。  
> 状态：✅ 2026-10-26  
> 代码：`slowline.py`、`l2_events.py`、`test_l2_slow_channels.py`、
> `scripts/generate_l2_golden.py`、`fixtures/l2_golden.json`、
> `fixtures/l2_golden_legacy.json`。

## 1. 范围

L2 回答四个问题：

1. **trust** 如何在关系里收敛、被背叛打断、被长期离线磨损？
2. **love** 在什么条件下生长、什么条件下被信任缺口侵蚀？
3. **longing** 如何离线生长、在线消退，并被 love 封顶？
4. **guilt** 在什么归因条件下才允许出现？

同时锁定 slow → fast 的交互口径，避免慢通道在空闲 tick 把快通道拉离人格基线。

包含：

- `grow_slow_channels()`：trust 向 `trust_target(love)` 收敛；love 受信任缺口影响；
- `damage_trust()`：背叛腰斩 trust，严重时二级侵蚀 love；
- `wake()` 的离线路径：快通道压缩衰减、longing 生长、7 天后 trust 微降；
- `interaction_baselines()`：trust 用关系均衡线，love / 快通道 / guilt 用人格基线；
- `GUILT_SELF_AGENCY_THRESHOLD = 0.3`；
- trust / love 的数值护栏；
- 快照保存 / 恢复慢通道状态与 legacy 开关。

不包含：

- tolerance / 伤疤 / 氛围 / 记忆 / Saga / LLM；
- L3 的两个顺序问题（氛围门控写入过晚、shock 密度采样过早；已由 L3 corrected 修复，L2 参考线不涉及）；
- 元情绪（L3.5）；
- 新的生产入口。`slowline.py` 只是参考线，`engine.py` 仍是组合根。

## 2. L2 基线决策（2026-10-26）

慢通道参与交互时，不能给所有通道套同一条基线：

| 通道 | 交互基线 | 原因 |
|---|---|---|
| trust | `trust_target(love)` | trust 是关系积分器，均衡点随 love 变化 |
| love | 人格基线 | 没有关系目标线；高于人格底色才算“额外依恋” |
| joy / sadness / anger / fear / disgust / surprise | 人格基线 | 人格底色不是正在发生的情绪 |
| guilt | 人格基线（0） | 基线为零，任何 guilt 都是事件激活 |

`gate_appraisal()` 里 trust 对 fear / anger / disgust 的绝对缓冲保持不变；
交互矩阵只负责“trust 高于关系均衡线”时额外压制 fear。

### 2.1 为什么不把 trust 也设成人格基线

最初尝试把 trust 与 love 都设为人格基线。结果：正常关系里 trust 会自然长到
`trust_target(love)`，交互矩阵把这段正常信任当成“高于基线的额外信任”，
每个空闲 tick 都压制 fear。

空闲 300 tick 的对照：

```text
trust 用人格基线：fear 0.105 → 0.000（无事件也持续下降）
trust 用 trust_target：fear 0.105 → 0.105（精确回到人格基线）
```

这不是权重问题，而是**关系均衡值和人格底色混为一谈**。因此：

- trust 的“正常值”是 `trust_target(love)`；
- love 没有对应的关系目标线，仍以人格基线为准。

### 2.2 为什么 `gate_appraisal` 的 trust 缓冲不删

`gate_appraisal()` 已经用绝对 trust 值缓冲 fear / anger / disgust。这是
“信任越高，越不容易被吓到 / 激怒 / 恶心”的基础关系效应，应该保留。

交互矩阵只补充相对关系均衡的调制：trust 高于 `trust_target(love)` 时，
才把这段“超额信任”用于 further suppress fear。两者职责不重叠。

## 3. trust 与 love 的动力学

### 3.1 trust

`grow_slow_channels()` 每 tick 做：

1. 计算 `target = trust_target(love)`；
2. `gap > 0` 时按 `gap × 0.02 + elapsed_hours × 0.0003` 靠近；
3. `gap ≤ 0` 时按 `gap × 0.005` 缓慢降回；
4. 最后 clamp 到 `[0, 1]`。

`trust_target(love) = 1 - 1 / (1 + 3.5 × love)`：

| love | trust_target |
|---:|---:|
| 0.1 | 0.10 |
| 0.3 | 0.22 |
| 0.6 | 0.50 |
| 0.9 | 0.78 |

### 3.2 love

- 信任缺口 `trust_target(love) - trust > 0.15`：love 按缺口侵蚀；
- 否则：love 缓慢生长，正面事件再给一次增量；
- clamp 到 `[0, 2]`。

正面事件的输入口径从**累计值**改为**本区间新增值**：

```python
# 旧：positive_events 是历史累计，30 天后每 tick 都吃到全部历史
positive_input = self.positive_events

# 当前：0 / 1，表示本次 tick 是否新增正面事件
positive_input = positive_now
```

旧口径会让 love 增量随历史事件数线性放大；长程模拟里 trust 被推过目标并撞到
1.0 上限。新口径下 30 天模拟收敛到 `trust ≈ trust_target(love)`。

### 3.3 正向交互上限

`love → trust` 与 `guilt → trust` 两条规则都可能加 trust。若每条规则各自
计算“到目标的剩余空间”，两条规则会一起越过目标。

`apply_interactions(..., trust_ceiling=...)` 用累计增量封顶：

```python
remaining = trust_ceiling - (snap["trust"] + trust_added)
delta = min(delta, max(0.0, remaining))
```

最终 trust 只能逼近、不能超过 `trust_target(love)`。

### 3.4 长程证据

30 天模拟（每 10 分钟一 tick，每 12 tick 一个正面事件）：

| 模式 | trust | love | trust_target(love) |
|---|---:|---:|---:|
| L2 当前 | 0.872741 | 1.959433 | 0.872741 |
| legacy | 1.000000 | 2.000000 | 0.875000 |

当前模式下 trust 精确收敛到关系均衡线；legacy 模式撞到两个硬上限。

## 4. guilt：0.3 自责门槛

guilt 不再是“只要有一点自责就产生弱愧疚”。规则是：

```text
self_agency = 1 - other_agency
self_agency ≤ 0.3  → guilt = 0
self_agency > 0.3  → 按剩余责任比例平滑放大
```

```python
guilt_agency = (self_agency - 0.3) / 0.7
Channel.GUILT = gr * max(0, -gc) * guilt_agency * (0.3 + cp * 0.2)
```

0.3 处用 `<= threshold + 1e-12` 防护，避免浮点误差产生 `2e-17` 的假愧疚。

固定负面事件下（`goal_relevance=0.9`、`goal_conduciveness=-0.8`、
`coping_potential=0.4`）：

| self_agency | 当前 guilt | legacy guilt |
|---:|---:|---:|
| 0.05 | 0.000000 | 0.013680 |
| 0.25 | 0.000000 | 0.068400 |
| 0.30 | 0.000000 | 0.082080 |
| 0.50 | 0.078171 | 0.136800 |
| 0.90 | 0.234514 | 0.246240 |
| 1.00 | 0.273600 | 0.273600 |

纯外部归因（`self_agency=0.05`）在引擎和参考线里都恒为 0；legacy 模式可复现
旧行为。

## 5. longing 与离线

- 在线：`longing -= 0.3 × elapsed_hours`，下限 `love × 0.05`；
- 离线：`hours > 1` 且 `love > 0.05` 时，
  `longing += love × log10(1 + hours) × 0.4`，上限 `love × 2`；
- 没有 love 就没有 longing；
- trust 在离线超过 7 天后，每多一周 × 0.95；
- 离线快通道使用 `OFFLINE_COMPRESSION` 压缩时间衰减，不做在线基线回弹。

## 6. 参考线与 golden

`slowline.py` 的 `SlowLine` 是 L2 参考实现：

```python
from slowline import SlowLine
from l2_events import EVENTS

line = SlowLine()                 # OCEAN 全 0.5
steps = line.run(EVENTS)          # 在线 / 离线混合
```

每个 `L2Step` 记录：

| 字段 | 含义 |
|---|---|
| `values` | 十通道原始值 |
| `dominant` | 表达候选通道内最高且 > 0.3 |
| `shock_channels` / `blends` | 冲击通道 / 混合标签 |
| `raw_appraisal` / `gated_appraisal` | 七通道（六快 + guilt）中间值 |
| `trust_target` | 本 tick 的关系均衡线 |
| `slow_positive_input` | 本 tick 喂给 love 的正面事件数 |

`l2_events.py` 共 **19 条**冻结事件：

| 类别 | 事件 |
|---|---|
| 基线附近 | `routine_notice` |
| 正面关系 | `praised_by_trusted`、`shared_quiet_time` |
| 负面关系 | `cold_shoulder`、`betrayal` |
| guilt 门槛 | `external_blame`、`low_self_agency`、`self_blame` |
| 快通道边界 | `boundary_violation`、`unexpected_threat`、`social_humiliation` |
| 离线 | `offline_2h`、`offline_24h`、`offline_8d`、`offline_30d` |
| 回归修复 | `reunion`、`after_long_absence`、`repair_self_blame`、`final_neutral` |

双 golden：

| 文件 | 模式 | 用途 |
|---|---|---|
| `fixtures/l2_golden.json` | 当前 L2 | 回归基线 |
| `fixtures/l2_golden_legacy.json` | 旧慢通道 + 绝对阈值交互 | 前后对照 |

每套都包含 `isolated`（单事件从基线跑一次）和 `sequence`（19 条连续跑）。
golden 误差 < `1e-9`。

生成 / 校验：

```bash
python scripts/generate_l2_golden.py
python scripts/generate_l2_golden.py --check
python scripts/generate_l2_golden.py --legacy
python scripts/generate_l2_golden.py --legacy --check
```

golden 是当前行为基线，不是理论正确值。只有有意修改慢通道公式或 slow → fast
语义时才重新生成，并逐条检查 diff。

### 6.1 sequence 关键读数

| 事件 | trust | love | longing | guilt | trust_target |
|---|---:|---:|---:|---:|---:|
| betrayal | 0.078789 | 0.211605 | 0.000000 | 0.000000 | 0.425491 |
| self_blame | 0.073155 | 0.163534 | 0.000000 | 0.246857 | 0.364017 |
| external_blame | 0.002536 | 0.179624 | 0.000000 | 0.000000 | 0.386007 |
| offline_30d | 0.475533 | 0.576825 | 1.008107 | 0.000000 | 0.668752 |
| final_neutral | 0.670965 | 0.587799 | 0.858107 | 0.146015 | 0.672914 |

这些数字说明：

- 背叛后 trust 远低于目标，love 被二级侵蚀；
- 外部归因 + 低自责事件不产生 guilt；
- 30 天离线后 longing 受 `love × 2` 约束，trust 继续向目标恢复；
- 结尾 trust 0.670965 ≤ target 0.672914，没有越过均衡线。

## 7. legacy 开关

| 开关 | 恢复的旧行为 |
|---|---|
| `legacy_slow_channels=True` | guilt 无 0.3 门槛；love 用累计正面事件；trust 交互回到人格基线且不设 target 上限 |
| `legacy_interactions=True` | 交互矩阵回到 v0.7 绝对阈值语义 |

两者同时为 `True` 时最接近 L1.1 之前的旧曲线，`l2_golden_legacy.json` 即此模式。

快照会持久化两个开关，恢复后继续沿同一条曲线运行。

## 8. 测试与验收

```bash
python -m unittest test_l2_slow_channels.py -v
# 32 tests, OK
```

覆盖：

- 19 条事件的 isolated / sequence 双 golden；
- golden 元数据锁定：guilt 门槛 0.3 vs 0.0、per-tick vs cumulative、
  trust 基线 `trust_target(love)` vs personality、相对 vs 绝对交互；
- guilt 在 0.3 及以下恒为 0，0.3 以上单调；外部归因在引擎里始终为 0；
- trust 收敛且不越过目标；love 在信任缺口大时侵蚀、健康时生长；
- 正面事件按区间输入，不再累计；
- trust / love 数值护栏；
- `interaction_baselines()` 只替换 trust，不替换 love；
- trust 处于均衡线时不压 fear，高于均衡线时才压；
- `love → trust` / `guilt → trust` 的累计上限；
- 抑制幅度按激活而非绝对值；
- longing 离线生长 / 在线消退、无 love 不生长、受 love 封顶；
- 7 天后 trust 微降；
- 引擎空闲 300 tick 六快通道回到基线；
- 30 天有界模拟；
- 快照保留慢状态与 legacy 开关，恢复后曲线连续。

全量回归：

```text
134 tests, OK（L2 完成时点）
166 tests, OK（L3 完成后）
L1 / L2 current + legacy 四套 golden 均未改变
```

四套 golden 全部逐位通过：

```text
l2_golden.json
l2_golden_legacy.json
l1_golden.json
l1_golden_legacy.json
```

L2 没有进入 L1 参考线；L1 的 46 条事件与两套 golden 均未改变。`engine.py`
与 `characters/*.py` 的 demo 退出码均为 0。

## 9. 已知问题与边界

- `dominant` 仍是“候选通道最高原始值且 > 0.3”，joy 基线 0.35 会偏向显示
  `joy`。这是 L1 遗留问题，L2 不另造规则。
- `grow_slow_channels()` 保留了未使用的 `events_today` 参数，兼容旧调用；
  语义以 `positive_count` 为准。
- 长程测试验证的是**有界与收敛**，不是“30 天后情绪恰好等于某个理论值”。
- 慢通道不参与快通道半衰期，也不在 tolerance load 中重复计数；它们通过
  `gate_appraisal()` 与交互矩阵影响快通道。

## 10. 下一步

1. L3 已完成：氛围门控前移到 state 写入前，shock 密度改为检测后采样；
   L1 / L2 四套 golden 逐位未变，旧顺序由 `legacy_atmosphere=True` 保留。
   见 [`L3_ATMOSPHERE_SCARS.md`](L3_ATMOSPHERE_SCARS.md)。
2. L3.5 元情绪默认关闭时，L1 / L2 四套 golden 与 L3 corrected / legacy A/B 场景必须不变。
3. 不接 JEV / LLM；不改 tolerance 参数；不动关系记忆。

## 11. 相关文档

- [`CORE.md`](CORE.md) — L0 纯动力学与 StepTrace
- [`L1_FAST_CHANNELS.md`](L1_FAST_CHANNELS.md) — 六条快通道与 L1.1 修复
- [`L3_ATMOSPHERE_SCARS.md`](L3_ATMOSPHERE_SCARS.md) — L3 氛围与伤疤
- [`INCREMENTAL_PLAN.md`](INCREMENTAL_PLAN.md) — L0–L6 + L3.5 开发计划
- [`META_EMOTION.md`](META_EMOTION.md) — L3.5 元情绪设计与 v0 实现
- [`../README.md`](../README.md) — 项目总览
