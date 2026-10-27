# L1 六条快通道

> 目标：把 joy / sadness / anger / fear / disgust / surprise 锁成一条可复现、
> 可回归、可对比的基础情绪线。  
> 状态：✅ 2026-10-25 建立 golden；L1.1（2026-10-26）修复空闲交互地板与
> surprise→joy 凭空加 joy，双 golden 保留修复前后对照。  
> 代码：`fastline.py`、`l1_events.py`、`test_l1_fast_channels.py`、
> `test_interaction_activation.py`、`fixtures/l1_golden.json`、
> `fixtures/l1_golden_legacy.json`。

## 1. 范围

L1 只回答一个问题：

> 给定固定人格、固定关系常数和一次 `Appraisal`，六条快通道如何衰减、评估、交互？

包含：

- 弹性衰减 + 基线回弹；
- `appraise()` → `gate_appraisal()` → 饱和写入；
- 六通道之间的交互：surprise→fear、surprise→joy、disgust→anger、joy+sadness 混合标签；
- 冲击检测（指数 Δ + 通道阈值）；
- 固定 10 分钟步长、固定 `now` 注入。

不包含：

- trust / love / longing / guilt 的动力学；
- slow → fast 交互（trust→fear、love+fear 等），留给 L2；
- tolerance / 伤疤 / 氛围 / 记忆 / Saga / LLM；
- 生产引擎的组合顺序问题（已由 L3 corrected 修复，L1 参考线不涉及）。

`trust = love = 0.5` 只作为 `gate_appraisal()` 的固定参数；交互前会把
trust / love / guilt 临时置零，避免慢通道数值影响 L1 曲线。

## 2. 参考线

`fastline.py` 的 `FastLine` 是 L1 的唯一参考实现：

```python
from fastline import FastLine
from l1_events import EVENTS

line = FastLine()                       # OCEAN 全 0.5，trust/love 0.5
steps = line.run(EVENTS, elapsed_minutes=10.0)
```

每个 `FastLineStep` 记录：

| 字段 | 含义 |
|---|---|
| `values` | 六通道原始值 |
| `dominant` | 六通道内最高且 > 0.3；与 `engine._dominant_channel` 同规则 |
| `shock_channels` | 本 tick 触发冲击的通道 |
| `blends` | 交互矩阵产生的混合标签 |
| `raw_appraisal` | `appraise()` 输出（仅六通道） |
| `gated_appraisal` | trust/love 门控后的激活（仅六通道） |

`idle_interactions` 默认 `True`：生产引擎每个 tick 都跑交互，L1.1 之后
空闲 tick 也不会产生交互增量。需要复现 v0.7 旧曲线时使用
`FastLine(legacy_interactions=True, idle_interactions=True)`。

## 3. 事件集

`l1_events.py` 共 **46 条**冻结事件：

| 来源 | 数量 | 说明 |
|---|---:|---|
| 芙宁娜 | 11 | 表演 / 独处 / 被看见 |
| 珊瑚宫心海 | 9 | 军师 / 社交焦虑 / 责任 |
| 哥伦比娅 | 11 | 月神 / 疏离 / 被需要 |
| engine.py demo | 3 | 夸奖 / 放鸽子 / 伤害朋友 |
| L1 synthetic | 12 | 意外度、归因、应对、社交评价、纯负面等边界 |

事件是**冻结副本**，不动态导入角色模块。角色文件以后改触发值，L1 golden
不会跟着漂移；要改必须显式改 `l1_events.py` 并重新生成 golden。

## 4. Golden

两套 golden，分别锁定修复后与修复前：

| 文件 | 模式 | 用途 |
|---|---|---|
| `fixtures/l1_golden.json` | `relative_to_baseline` | 当前默认行为，L1 回归基线 |
| `fixtures/l1_golden_legacy.json` | `legacy_absolute` | v0.7 绝对阈值行为，前后对照 |

每套都包含：

- `isolated`：每条事件从基线单独跑一次，保留 raw / gated 中间值；
- `sequence`：46 条事件按固定顺序连续跑，检查累积、衰减和交互。

生成 / 校验：

```bash
python scripts/generate_l1_golden.py                    # 写入当前 golden
python scripts/generate_l1_golden.py --check            # 校验当前 golden
python scripts/generate_l1_golden.py --legacy           # 写入旧行为 golden
python scripts/generate_l1_golden.py --legacy --check   # 校验旧行为 golden
```

golden 是**当前行为基线**，不是理论正确值。只有有意修改 L0 公式或 L1 参考线时
才能重新生成，并在 diff 中逐条检查。

## 5. L1.1 交互修复记录

### 5.1 根因

旧 `apply_interactions()` 用**绝对通道值**做门槛（`< 0.05`），而人格基线里
surprise 是 0.10、disgust 是 0.06。基线被当成“正在发生的情绪”，于是：

- 空闲 tick 也持续触发 surprise→joy、surprise→fear、disgust→anger；
- 正面事件会被 surprise→fear 加上一段恐惧；
- 纯负面事件会被 surprise→joy 加上一段喜悦。

这不是权重问题，而是**把人格底色和情绪激活混为一谈**。

### 5.2 修复

`apply_interactions()` 新增可选 `baseline`：

- 源通道激活 = `max(0, value - baseline)`；
- 只有激活超过 `threshold = 0.05` 才参与交互；
- 快通道放大规则要求目标也已激活（co-activation）：
  惊讶只能放大已经存在的喜悦/恐惧，不能从零制造；
- 抑制规则不要求目标激活：愤怒仍可压低处于基线的信任；
- 混合标签要求双方都高于基线；
- 不传 `baseline` 时保持 v0.7 绝对阈值行为。

`EmotionEngine` 默认传入 `Personality.baseline()`；`legacy_interactions=True`
可复现旧行为。`FastLine` 同样支持 `legacy_interactions`。

### 5.3 修复前后对照

空闲 300 tick（固定 trust/love、trust/love 不参与交互）：

| 通道 | 修复前 | 修复后 |
|---|---:|---:|
| joy | 0.4220 | 0.3500 |
| fear | 0.1920 | 0.1050 |
| anger | 0.1239 | 0.0550 |
| sadness | 0.1250 | 0.1250 |
| disgust | 0.0600 | 0.0600 |
| surprise | 0.1000 | 0.1000 |

修复后六通道全部精确停在人格基线。

46 条 isolated 事件的最大绝对差值：

```text
joy 0.115717   anger 0.066675   fear 0.173943
sadness 0.000000   disgust 0.000000   surprise 0.000000
```

变化最大的是正面事件：旧行为给它们额外加了 fear / anger；修复后这些
“无来源负面情绪”消失。`synthetic.pure_negative_zero_cues` 的 joy 从
0.4119 回到基线 0.3500，同时 sadness / fear / anger 保持原有事件反应。

注意：生产引擎里 trust 长期高于基线时，`trust→fear` 仍会压低 fear，
这是慢通道对快通道的调制，属于 L2 范围，不是空闲交互地板。

### 5.4 剩余已知问题：dominant 的基线偏差

`dominant` 沿用 `engine._dominant_channel`：六通道原始值最高且 > 0.3。
joy 基线 0.35，因此基线状态和许多事件后都会显示 `joy`，即使负面通道正在上升。
L1 先保持与引擎一致，不在这里另造一套 dominant 规则。

## 6. 测试与验收

```bash
python -m unittest test_l1_fast_channels.py -v
# 22 tests, OK
python -m unittest test_interaction_activation.py -v
# 11 tests, OK
```

覆盖：

- 46 条事件数量、来源、key 唯一、Appraisal 取值范围；
- isolated / sequence golden（六通道 + dominant + shock + blends + raw/gated），误差 < 1e-9；
- legacy golden 逐位复现 v0.7 行为；
- 六通道连续 46 事件无 NaN / Inf / 负值；
- 无事件 200 tick 回到基线；
- 空闲 tick 不再产生交互地板；
- 纯负面事件的 appraisal joy = 0，且交互层不再凭空加 joy；
- 意外正面事件的 joy 高于同等预期事件（共激活仍生效）；
- 意外度提高 → surprise 提高；
- 低应对 → fear 提高；外部归因 → anger 提高；fear+anger 占比提高；
- 抑制规则可在目标处于基线时生效；
- 慢通道在交互后还原，guilt / longing 保持 0；
- 生产引擎空闲 tick 不抬高 joy / anger，legacy 引擎仍复现旧地板。

全量回归：

```text
102 tests, OK（L1.1 时点）
166 tests, OK（L2 + L3 完成后）
L1 current / legacy golden 均未改变
```

L1.1 修改了 `core.apply_interactions()` 的**触发语义**，但没有改任何交互权重；
`engine.py` 只增加开关与 baseline 传参。旧曲线由 legacy golden 锁定。

## 7. 下一步

1. L2 已完成：trust 用 `trust_target(love)` 作交互基线，love / 快通道 / guilt 用人格基线；见 [`L2_SLOW_CHANNELS.md`](L2_SLOW_CHANNELS.md)。
2. L3 已完成：修正氛围 / shock 顺序时，L1 双 golden 逐位未变；见 [`L3_ATMOSPHERE_SCARS.md`](L3_ATMOSPHERE_SCARS.md)。
3. L3.5 元情绪默认关闭时，L1 golden 必须完全不变。

## 8. 相关文档

- [`CORE.md`](CORE.md) — L0 纯动力学与 StepTrace
- [`L2_SLOW_CHANNELS.md`](L2_SLOW_CHANNELS.md) — L2 慢通道与 slow → fast 基线
- [`L3_ATMOSPHERE_SCARS.md`](L3_ATMOSPHERE_SCARS.md) — L3 氛围与伤疤
- [`INCREMENTAL_PLAN.md`](INCREMENTAL_PLAN.md) — L0–L7 + L3.5 开发计划
- [`META_EMOTION.md`](META_EMOTION.md) — L3.5 元情绪设计与 v0 实现
- [`../README.md`](../README.md) — 项目总览
