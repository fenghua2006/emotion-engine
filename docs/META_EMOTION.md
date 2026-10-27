# 元情绪（Meta-Emotion）

> 状态：**L3.5 v0 已实现（默认 trace-only）**；L5 可选消费 `tolerance_load_bias`（默认关闭）。
> 代码：`meta_emotion.py`；集成：`engine.py`；角色示例：`characters/furina.py`、`characters/meta_emotion_demo.py`；测试：`test_l3_5_meta_emotion.py`。
> 层：**L3.5**（L3 氛围/伤疤之后，L4 记忆之前）。
> 关联：`docs/CORE.md`、`docs/TOLERANCE.md`、`docs/INCREMENTAL_PLAN.md`、`core.py`、`engine.py`。

默认边界仍然很窄：元情绪只读最终状态与 `StepTrace`，结果只进入 trace；
不改变基础通道、tolerance、氛围、表达输出或 `Personality`。L5 新增了一个
显式开关，允许把 `tolerance_load_bias` 作为有界附加负荷；它默认关闭，
不影响旧曲线，也不消费表达增益或 display rule。

## 1. 一句话结论

**元情绪不是第 11 个情绪通道，而是“以某个 primary emotion 为对象”的二阶评估与体验。**  
它读取基础情绪，输出表达、调节、耐受与关系行为的**有界偏置**，不直接改写 `core.py` 的通道公式。

## 2. 设计根因

### 2.1 为什么不能新增一组并列通道

把 `shame_about_anger`、`guilt_about_joy` 直接做成新通道，会犯三个错误：

1. **层级混淆**：`anger` 与 `shame about anger` 不在同一层。前者是事件评估的结果，后者是对这个结果的再评估。
2. **组合爆炸**：10 个基础通道 × 若干元情绪 = 几十个通道；每个都要定义衰减、基线、交互、耐受，公式会迅速失控。
3. **解释力下降**：同一个 `anger = 0.7`，在不同角色身上可能是“骄傲地发火”“羞耻地压住”“恐惧地道歉”。差别不在 anger 大小，而在元情绪。

### 2.2 为什么 valence 应该放在二阶层

传统做法把正/负效价直接贴在基础情绪上：joy 正、anger 负。但 2026 年 van Kleef & Sauter 的综述指出：**基础情绪层面的 valence 预测力很弱**——负面情绪可能带来好结果，正面情绪也可能带来坏结果。

更有解释力的是**元情绪层面的 valence**：

- 我是否喜欢自己正在感受的东西？
- 这个情绪是否符合我的目标、身份、关系规范？
- 我是否想表达、压抑、修复或利用它？

因此，valence 不应该继续加在基础通道上，而应该成为 **meta-emotion 的一阶字段**。这是本设计的核心根因。

### 2.3 两层结构

```text
第一层：primary emotion
  Appraisal（事件六维）→ 10 通道（joy / sadness / anger / fear / ...）

第二层：meta-emotion
  以某个 primary channel 为对象
  → 元评估（goal / social / control / identity）
  → 元情绪（shame / guilt / pride / contempt / compassion / interest / fear）
  → 调节意图（accept / suppress / ruminate / express / repair / avoid）
  → 有界偏置（表达、耐受负荷、关系行为）
```

第二层不替代第一层，也不能把第一层“抵消”掉。它的作用是**解释角色如何对待自己的情绪**。

## 3. 文献地图

主流文献没有统一的“元情绪 N 种”清单，而是五条互补传统。

| 传统 | 核心问题 | 主流维度 / 类型 | 代表 |
|---|---|---|---|
| 父母元情绪理念（PMEP） | 父母如何看、如何回应自己与孩子的情绪 | 觉察、接受、教导；教练型 / 摒弃型 / 不干涉 / 失控 | Gottman, Katz & Hooven 1996, 1997；叶光辉 2002 |
| 元情绪体验 | 对自身情绪产生什么情绪 | MES 六因子：愤怒、关怀、兴趣、轻蔑/羞耻、强硬控制、压抑；正/负元情绪指数 | Mitmansgruber et al. 2009 |
| 元情绪四象限 | 元情绪与原情绪的效价组合 | 负-负、负-正、正-负、正-正 | Bailen, Wu & Thompson 2019 |
| 元心境 / 元情绪能力 | 能否注意、区分、修复情绪 | 注意、清晰、修复 | Salovey et al. 1995（TMMS） |
| 元情绪信念 / 调节 | 对情绪本身持什么信念、如何调节“调节行为” | 情绪危险/不可控/无用/可耻；接受、压抑、反刍、回避；HiTOM | Strodl et al. 2023；Kraft et al. 2025 |

### 3.1 四象限

第一列是“对情绪的感受”，第二列是“被感受的情绪”：

| 元情绪效价 | 原情绪效价 | 例子 |
|---|---|---|
| 负 | 负 | 为自己的愤怒羞耻；为焦虑而焦虑；为悲伤尴尬 |
| 负 | 正 | 为自己的快乐内疚；为骄傲羞耻；害怕幸福 |
| 正 | 负 | 为自己的愤怒自豪；对恐惧感兴趣；接纳自己的悲伤 |
| 正 | 正 | 为自己的爱高兴；为平静骄傲；对 relief 感恩 |

Bailen et al. (2019) 的日常经验取样发现：约 53% 的参与者至少出现过一次元情绪，平均每周约两次；**负-负型最常见**，并与抑郁严重度相关。四象限是描述性分类，不是经过验证的穷尽 taxonomy。

### 3.2 MES 六因子

Mitmansgruber et al. (2009) 的 Meta-Emotion Scale 把元情绪拆成六个可测量倾向：

1. **Anger**：对自己有情绪而生气；
2. **Compassionate care**：对自己的痛苦有自我关怀；
3. **Interest**：对自己的情绪好奇、感兴趣；
4. **Contempt/Shame**：对自己的情绪轻蔑或羞耻；
5. **Tough control**：强硬要求自己立刻控制住；
6. **Suppression**：压抑情绪。

这六因子是测量产物，不是“人类只有六种元情绪”的本体论。工程上可作为标签集合，不应硬编码为六个新通道。

### 3.3 相关但不同的构念

- **TMMS / 元心境**：注意、清晰、修复，是能力/特质，不是“对情绪的情绪”。
- **元情绪信念**：情绪是危险的、不可控的、无用的、可耻的、无效的；更接近认知 schema。
- **元情绪调节（MER）**：调节的是“情绪调节行为”，不是情绪本身；与情绪调节（ER）不同。
- **元情绪智力（MEI）**：元情绪知识、元认知自我评价、元情绪信念；更偏自我评估能力。
- **临床近亲**：fear of fear、anxiety sensitivity、depression about depression、fear of happiness、survivor guilt。

### 3.4 共识与边界

- 元情绪必须是二阶的：没有 primary emotion，就谈不上严格的 meta-emotion。
- 负向元情绪不必然有害；在某些职业/关系语境下，轻蔑、羞耻、强硬控制可能有适应性。
- 没有 gold standard；测量传统异质，四象限与六因子都不能当成完整本体论。
- 元情绪 ≠ 情绪调节 ≠ 元认知 ≠ 情绪智力；边界仍有争议，工程实现必须明确自己的定义。

## 4. 引擎建模决定

### 4.1 最小状态

```python
MetaEmotion(
    target="anger",          # 被指向的 primary channel
    kind="shame",            # shame / guilt / pride / contempt / compassion / interest / fear
    valence=-0.8,            # 对这个情绪的感受
    intensity=0.6,
    acceptance=0.1,          # 接受 vs 压抑
    control=0.2,             # 我能不能承受 / 调节
    action="suppress",       # accept / suppress / ruminate / express / repair / avoid
)
```

v0 实际实现的字段：

| 字段 | 范围 / 取值 |
|---|---|
| `target` | 10 个 `Channel` 之一 |
| `kind` | `shame / guilt / pride / contempt / compassion / interest / fear` |
| `valence` | 固定标签效价，−1..1 |
| `intensity` | 0..1，经过 profile 权重后的 kind 分数 |
| `acceptance` | 0..1，`0.45*control + 0.35*social + 0.20*identity` |
| `control` | 0..1，对应 `MetaAppraisal.controllability` |
| `action` | 六个调节动作之一 |

`belief` 由角色档案或未来记忆层提供，v0 不进入公式，也不进入 trace。

同时实现 `MetaAppraisal`、`RegulationBias`、`MetaEmotionProfile`、`MetaEmotionState`，
全部可序列化为纯 dict/list；`MetaEmotionState` 只属于当前 tick，不存到 engine 上。

### 4.2 元评估四维

复用 `Appraisal` 的思路，但对象换成“情绪本身”：

| 维度 | 问题 | 高值含义 |
|---|---|---|
| `goal_conduciveness` | 这个情绪对我当前目标有利吗？ | 有用 / 有帮助 |
| `social_acceptability` | 在这个关系、场合里能被接受吗？ | 被允许 / 被认可 |
| `controllability` | 我能不能承受、调节它？ | 可承受 / 可修复 |
| `identity_congruence` | 它符合“我是谁”吗？ | 符合自我 / 角色 |

**激活规则**：不是“绝对值超过 0.3”，而是相对该通道自己的参考值。

- `trust` 的参考值是 `trust_target(love)`；其他通道用人格基线。
- `felt_diff = felt(value) − felt(reference)`，`raw_excess = value − reference`。
- 仅当 `felt_diff ≥ 0.04` 且 `raw_excess ≥ 0.03` 时激活；
  `activation = clamp01(felt_diff / 0.25)`。

这样既避免高基线通道（joy / trust）被当成“强烈情绪”，也避免低基线通道的
微小数值波动触发元情绪。激活只决定“是否值得二阶评估”，不决定 kind。

**四维公式**（全部 clamp 到 `[0,1]`；`support = 0.6·trust + 0.4·min(1,love)`）：

```text
goal = GOAL_BASE[target]
     + 0.25·event_gc·valence_sign[target]
     + 0.15·(event_coping − 0.5)·target_factor
     + guilt 专属 self_agency 项
     − 0.08·scar_target

social = 0.5 + 0.35·event_social
       + 0.15·(support − 0.5)
       − 0.20·max(0, atmosphere)
       + DISPLAY_NORM[target]
       − 0.10·scar_target

control = 0.5 + 0.25·(event_coping − 0.5)
        + 0.15·(support − 0.5)
        − 0.25·activation
        − 0.20·overload_ratio
        − zone_penalty
        − 0.10·scar_target

identity = 0.5 + 0.10·event_social
         − 0.10·max(0, atmosphere)
         + 0.05·(openness − 0.5)
         − 0.05·(neuroticism − 0.5)
         + 0.10·event_gc·valence_sign[target]
```

`zone_penalty`：regulated `0` / hyper `0.25` / hypo `0.15`。
`GOAL_BASE` 与 `DISPLAY_NORM` 是通道级通用先验，不是角色 profile，也不规定
“anger 必然是 shame”。v0 取值如下：

| target | `GOAL_BASE` | `VALENCE_SIGN` | `DISPLAY_NORM` |
|---|---:|---:|---:|
| joy | 0.62 | +1 | +0.10 |
| sadness | 0.38 | −1 | −0.10 |
| anger | 0.50 | −1 | −0.10 |
| fear | 0.45 | −1 | −0.10 |
| disgust | 0.42 | −1 | −0.10 |
| surprise | 0.55 | 0 | 0.00 |
| guilt | 0.48 | −1 | −0.10 |
| longing | 0.45 | 0 | 0.00 |
| love | 0.62 | +1 | +0.10 |
| trust | 0.65 | +1 | +0.10 |

**标签分数**：先取四维相对 0.5 的缺口 / 超出量，
`goal_low = max(0, 0.5−goal)` 等；`self_agency = 1 − other_agency`；
`threat` 由负性均值、正氛围与 hyper zone 合成；`support` 同上。

```text
shame      = activation·(1.8·social_low + 1.2·identity_low + 0.6·goal_low)
             ·(0.6 + 0.4·control_low)
guilt      = activation·(1.8·goal_low + 0.8·social_low)
             ·(0.5 + 0.5·self_agency)·(0.6 + 0.4·control_low)
pride      = activation·(1.8·goal_high + 0.9·social_high + 0.6·identity_high)
             ·(0.6 + 0.4·control_high)
contempt   = activation·(1.5·social_low + 1.0·identity_low)
             ·(0.6 + 0.4·control_high)
compassion = activation·(0.6 + goal_low)·(0.3 + 0.7·control_high)
             ·(0.5 + 0.5·support)·（负性 target 权重 1.0，否则 0.25）
interest   = activation·(0.35·surprise_salience + 0.25·openness
             + 0.20·(1−threat) + 0.10·mixedness)·0.6
fear       = activation·(1.4·control_low + 0.4·goal_low)
             ·(0.5 + 0.5·threat)
```

所有分数 clamp 到 `[0,1]`，乘 profile 权重后再阈值化：阈值 `0.18`，
每个 target 最多保留 3 个 kind；多个 kind 可同时存在，不归一化、不抵消。

**动作分数**（确定性取最大值，平分时按 `accept / suppress / ruminate /
express / repair / avoid` 顺序）：

```text
accept   = 0.55·compassion + 0.35·interest + 0.20·pride + 0.20·control
suppress = 0.75·shame + 0.45·contempt + 0.40·fear + 0.20·(1−control)
ruminate = 0.75·guilt + 0.40·fear + 0.30·shame + 0.20·(1−control)
express  = 0.75·pride + 0.40·interest + 0.25·social + 0.20·control
repair   = 0.80·guilt + 0.45·compassion + 0.20·control
avoid    = 0.70·fear + 0.40·contempt + 0.30·shame + 0.20·(1−control)
```

profile 的 `action_bias[target]` 只给指定动作加 `0.25`，不是硬覆盖。
输出：

- `expression_gain[target] = clamp(1 + Σ kind_gain·intensity, 0.5, 1.5)`；
- `display_rule`：`accept/express → amplify`，`suppress/avoid → suppress`，
  `repair/ruminate → qualify`；
- `tolerance_load_bias = clamp(Σ suppress/ruminate/avoid 的 0.06·intensity, 0, 0.2)`；
- `reappraisal_target`：主导 kind 对应的四维，去重后最多 2 项。

默认 `tolerance_meta_load_enabled=False`：`tolerance_load_bias` **只记录，不喂给
tolerance**。L5 开启后，它才作为 `[0, 0.2]` 的附加负荷进入 instant load；表达
增益和 display rule 仍**不喂给 `respond()`**。

### 4.3 输出只做有界偏置

元情绪层不直接写 raw channel。它输出：

```python
{
    "expression_gain": {"anger": 0.657},
    "display_rule": {"anger": "suppress"},
    "regulation_action": {"anger": "suppress"},
    "tolerance_load_bias": 0.035,
    "reappraisal_target": ["social_acceptability", "identity_congruence"],
}
```

上面只列 `anger` 一个 target 的示例值；实际 trace 会同时列出所有激活的
target（demo 中还有 sadness / fear / disgust / surprise，总负荷偏置 0.0972）。

约束：

- 所有增益都在显式区间内：`expression_gain ∈ [0.5, 1.5]`，
  `tolerance_load_bias ∈ [0, 0.2]`，`kind.intensity / acceptance / control ∈ [0, 1]`；
- 任何会改变曲线的参数必须有开关、默认关闭、前后对比；
- 元情绪不能写 `Personality`，不能改 `baseline()`；
- 元情绪不能直接改 `core.py` 的 `decay()`、`appraise()`、`gate_appraisal()`；
- **默认没有实际消费点**：`regulation_bias` 只作为 trace 观测，不进入
  `respond()` 或下一 tick 的 appraisal。
- L5 唯一例外：`meta_emotion_enabled=True`、`tolerance_meta_load_enabled=True`、
  非 legacy 且 `capacity_model="multi"` 时，`tolerance_load_bias` 进入
  `tolerance.update()`；使用上一 tick 的 zone / overload，避免同 tick 循环。
  关闭开关或 legacy 时路径与 v0 相同。

### 4.4 StepTrace 扩展

L3.5 在 `StepTrace` 增加四个字段：

| 字段 | 含义 |
|---|---|
| `meta_enabled` | 本次 trace 是否计算了元情绪 |
| `meta_appraisal` | 对 primary emotion 的四维元评估（key = target） |
| `meta_emotions` | 本 tick 激活的元情绪列表 |
| `regulation_bias` | 表达/调节/耐受的有界偏置 |

仍然遵守 L0 契约：

- `trace=False` 行为完全不变，也不计算元情绪；
- `meta_emotion_enabled=False` 时，trace 仍只有原来的 27 个键，新增字段不序列化；
- 只有 `meta_emotion_enabled=True` 且 `trace=True` 时，`to_dict()` 才追加四个键；
- 不新增 `mark()` phase，不增加 top-level `result["meta_emotion"]`。

**开启方式**：

```python
engine = EmotionEngine(
    ...,
    meta_emotion_enabled=True,
    meta_emotion_profile=None,  # None = 中性 profile
)
result = engine.tick(appraisal, trace=True)
result["trace"]["meta_emotions"]
```

单独传 `meta_emotion_profile` 不会自动开启；必须显式设置
`meta_emotion_enabled=True`。

### 4.5 稳定 profile 与瞬时 state 分开

- **MetaEmotionProfile**：角色稳定的元情绪倾向，例如“恐惧→羞耻”“愤怒→骄傲”“快乐→内疚”。放在角色档案里，不写 `Personality`。
- **MetaEmotionState**：当前事件触发的瞬时元情绪。

v0 的 profile schema：

```python
MetaEmotionProfile(
    name="furina_meta_shame",
    kind_weights={
        "anger": {"shame": 2.0, "pride": 0.2, "interest": 0.4, "fear": 0.8},
    },
    action_bias={"anger": "suppress"},
)
```

- `kind_weights[target][kind]` 乘在通用 kind 分数上，限制在 `[0, 2]`；
  未列出的 kind 权重为 `1.0`；未知 target / kind 忽略。
- `action_bias[target]` 只给指定动作加 `0.25`，不是硬覆盖；未知动作忽略。
- `profile=None` 等价于 `NEUTRAL_META_PROFILE`（`name="neutral"`，
  `kind_weights={}`，`action_bias={}`）：不预设“愤怒→羞耻”等倾向。
- **只传 profile 不会开启元情绪**；必须同时设置
  `EmotionEngine(meta_emotion_enabled=True)`。

**不持久化**：`MetaEmotionState` 只属于当前 tick；profile 与 enable 开关是
配置，不写入 snapshot。`load_snapshot()` 行为不变。

同一 `fear = 0.8` 的三种角色：

| 元情绪 | 行为倾向 |
|---|---|
| 羞耻于恐惧 | 压抑、掩饰、负荷升高、求助减少 |
| 对恐惧感兴趣 | 接近、观察、探索 |
| 接纳恐惧 | 承认、求助、修复 |

这比单纯调 OCEAN 基线更能制造角色差异。

## 5. 与各层的关系

| 层 | 与元情绪的关系 |
|---|---|
| L0 | 提供通道、评估、`StepTrace`；元情绪只读 trace，不改公式 |
| L1 | 六条快通道保持纯净；四套 golden 不引入元情绪 |
| L2 | 提供 guilt / trust / love，元情绪依赖这些慢通道与自我归因 |
| L3 | ✅ 修正氛围/伤疤顺序；元情绪读取稳定的 atmosphere / scar 状态 |
| **L3.5** | ✅ **v0 已实现（trace-only）：二阶评估 + 调节意图 + 有界偏置** |
| L4 | 记忆可提供元情绪信念与关系规范；元情绪不直接写记忆 |
| **L5** | ✅ 校准核心：可选用 `tolerance_load_bias`（`tolerance_meta_load_enabled=False`）；伤疤压 capacity、meta 加 load，不重复计 |
| L6 | 未来消费 `display_rule` / `expression_gain`；LLM 只表达，不生成元情绪 |

## 6. L3.5 v0 交付与验收

### 目标

让引擎能够回答：**“角色对这个情绪本身是什么态度？”**  
而不是只回答：**“角色现在有多愤怒？”**

### 已交付

| 文件 | 内容 |
|---|---|
| `meta_emotion.py` | 纯函数层：五个 frozen dataclass、激活规则、四维公式、七 kind、六 action、`meta_appraise()` / `meta_regulate()` / `evaluate_meta_emotion()` |
| `core.py` | `StepTrace` 增加 `meta_enabled` + 三个 meta 字段；仅开启时序列化 |
| `engine.py` | `meta_emotion_enabled=False`、`meta_emotion_profile=None`；trace 分支内调用 |
| `characters/furina.py` | `furina_meta_shame_profile`、`furina_meta_pride_profile`；`create_furina()` 可选参数 |
| `characters/meta_emotion_demo.py` | 同一 anger、同一状态、同一时钟 → shame/suppress vs pride/express |
| `test_l3_5_meta_emotion.py` | 25 个 `unittest`；覆盖兼容性、激活、数学、profile、调节、trace 安全 |

`belief`、记忆、LLM、JEV 均未进入 v0。

L5 后续接入（`docs/TOLERANCE.md`）：`EmotionEngine` 新增
`tolerance_meta_load_enabled=False`；开启且 meta 也开启时，tick 在耐受窗
更新前用上一 tick 的 zone / overload 计算一次 meta，`tolerance_load_bias`
经 `[0, 0.2]` 限幅后加入 instant load。`test_l5_tolerance.py` 覆盖开关组合、
调用次数、上一 tick 语义与不重复计伤疤。

### 验收结果

- ✅ `python -m unittest test_l3_5_meta_emotion.py -v`：**25 tests OK**（L3.5 基线）。
- ✅ L5.1 后全套：**241 tests OK**；L5 单独 **38 tests OK**，L5.1 **12 tests OK**。
- ✅ `tolerance_meta_load_enabled=False` 时 L3.5 trace 键集合、调用次数与
  post-update 计算路径不变。
- ✅ 开启后 meta 只计算一次（trace on/off 一致），并使用上一 tick zone / overload。
- ✅ snapshot 仍不持久化 meta state / profile / enable 开关；新增
  `legacy_tolerance` 与 multi tolerance 观测字段。
- ✅ 四套 golden `--check` 全部通过，`fixtures/` 无 diff：
  - `l1_golden.json` / `l1_golden_legacy.json`
  - `l2_golden.json` / `l2_golden_legacy.json`
- ✅ meta 关闭：trace 仍是原来的 27 个键，值逐位不变。
- ✅ meta 开启：只增加 `meta_enabled / meta_appraisal / meta_emotions / regulation_bias`。
- ✅ meta 开启 + `trace=False`：与关闭时结果逐值相同。
- ✅ meta 开启 + `trace=True`：pop 掉 `trace` 后，所有基础字段与关闭时逐值相同。
- ✅ L3 corrected：atmosphere `-0.200000`，shock density `0.714300`。
- ✅ L3 legacy：atmosphere `-0.3774803783593781`，sadness `0.6125356194066094`，
  anger `0.5079335958919840`，fear `0.6160595893163425`。
- ✅ 现有角色 demo（`engine.py`、`test_iloveyou.py`、`characters/furina.py`、
  `characters/kokomi.py`、`characters/columbina.py`）退出 0，输出行为不变。
- ✅ `characters/meta_emotion_demo.py` 退出 0：
  - shame profile → `anger meta: shame intensity=0.585 action=suppress`；
  - pride profile → `anger meta: pride intensity=0.340 action=express`；
  - 两个引擎的基础通道逐值一致。
- ✅ 连续 20 个正负事件无 NaN / Inf / 越界；无 top-level `result["meta_emotion"]`。
- ✅ snapshot 不持久化 meta state / profile / enable 开关。
- ✅ AST 边界测试：`meta_emotion.py` 只导入 stdlib + `core`。

### 验证命令

```bash
cd /c/Users/Administrator/Desktop/emotion-engine

python -m unittest test_l3_5_meta_emotion.py -v
python -m unittest discover -v

python scripts/generate_l1_golden.py --check
python scripts/generate_l1_golden.py --legacy --check
python scripts/generate_l2_golden.py --check
python scripts/generate_l2_golden.py --legacy --check

python -m unittest test_l3_atmosphere_scars.py -v

uv run python engine.py
uv run python test_iloveyou.py
uv run python characters/furina.py
uv run python characters/kokomi.py
uv run python characters/columbina.py
uv run python characters/meta_emotion_demo.py

git diff --stat -- fixtures/
git status --short
```

### 不做

- 不把表达增益 / display rule 应用到 `respond()`；
- 默认不把 `tolerance_load_bias` 应用到 tolerance；L5 只在显式开关下消费；
- 不新增基础情绪通道；
- 不接 JEV / LLM 生成元情绪；
- 不改 `core.py` 公式、`Personality`、tolerance 参数或 atmosphere / scar 逻辑；
- 不持久化 meta state、profile 或 enable 开关；
- 不新增 `StepTrace` phase，不增加 top-level `result["meta_emotion"]`。

L3.5 当时不新增 golden；L5 另建 `l5_tolerance_golden*`，不修改 L1/L2 四套 golden。

## 7. 开放问题与 v0 结论

1. **`acceptance` 与 `control` 是否独立？**
   v0 中 `acceptance = 0.45·control + 0.35·social + 0.20·identity`，两者同源、
   高度相关。这足以让 trace 可读，但还不能证明它们是独立构念；若未来要把
   `acceptance` 作为独立状态，需要先解耦公式并做对照测试。

2. **元情绪是否只对“显著高于基线”的通道激活？阈值是多少？**
   是。v0 使用双重阈值：`felt_diff ≥ 0.04` 且 `raw_excess ≥ 0.03`，
   `activation = clamp01(felt_diff / 0.25)`。`trust` 的参考值是
   `trust_target(love)`，其他通道用人格基线。阈值目前是工程值，未做
   golden-set 标定。

3. **离散 `kind` 还是连续向量？**
   v0 两者并存：连续值用于 kind / action 计算与强度，离散 `kind` 用于 trace
   与表达。`valence` 是 kind 的固定标签值，不是独立连续维度。

4. **元情绪是否影响下一 tick 的 appraisal 增益？**
   不影响 appraisal 本体。v0 的 `regulation_bias` 没有消费点；L5 只新增了
   tolerance load 的可选消费，默认关闭。表达增益仍留给 L6，并且必须新增
   开关、保持默认关闭、重跑 golden 与 A/B。

5. **元情绪与“伤疤”的区别？**
   伤疤是事件记忆对通道的放大，元情绪是对当前情绪的再评估。v0 只读取
   `scar_pressure` / `scar_channels` 来降低 goal、social、control。
   L5 中伤疤按事件类型压缩 capacity，meta 只增加 bounded load；两者可以
   同时生效，但不把同一来源重复计入负荷。

6. **角色档案是否由 LLM 蒸馏生成？**
   v0 手工配置 profile，未接 LLM。`MetaEmotionProfile` 只是可选的稳定偏好，
   默认中性；生成式蒸馏留到有可解释性校验之后再考虑。

另外两个 v0 边界：

- **profile 不是 Personality**：不写 OCEAN、不写 `baseline()`、不持久化。
- **trace-only 仍是默认契约**：L5 已为 tolerance 增加显式开关与 legacy 回滚；
  L6 消费表达 bias 时仍需单独设计开关与回滚路径，不能直接把当前 trace 值
  接到行为上。

## 8. 参考文献

- Gottman, J. M., Katz, L. F., & Hooven, C. (1996). Parental meta-emotion philosophy and the emotional life of families: Theoretical models and preliminary data. *Journal of Family Psychology, 10*(3), 243–268. https://doi.org/10.1037/0893-3200.10.3.243
- Gottman, J. M., Katz, L. F., & Hooven, C. (1997). *Meta-emotion: How families communicate emotionally*. Lawrence Erlbaum.
- Mitmansgruber, H., Beck, T. N., Höfer, S., & Schüßler, G. (2009). When you don’t like what you feel: Experiential avoidance, mindfulness and meta-emotion in emotion regulation. *Personality and Individual Differences, 46*(4), 448–453. https://doi.org/10.1016/j.paid.2008.11.013
- Bailen, N. H., Wu, H., & Thompson, R. J. (2019). Meta-emotions in daily life: Associations with emotional awareness and depression. *Emotion, 19*(5), 776–787. https://doi.org/10.1037/emo0000488
- Salovey, P., Mayer, J. D., Goldman, S. L., Turvey, C., & Palfai, T. P. (1995). Emotional attention, clarity, and repair: Exploring emotional intelligence using the Trait Meta-Mood Scale. In J. W. Pennebaker (Ed.), *Emotion, disclosure, and health* (pp. 125–154). American Psychological Association.
- Jäger, C., & Bartsch, A. (2006). Meta-emotions. *Grazer Philosophische Studien, 73*(1), 179–204.
- Mendonça, D. (2013). Emotions about emotions. *Emotion Review, 5*(4), 390–396. https://doi.org/10.1177/1754073913484373
- van Kleef, G. A., & Sauter, D. A. (2026). What makes emotions positive or negative? Repurposing valence at the level of meta-emotions. *Affective Science*. https://doi.org/10.1007/s42761-026-00385-6
- Lister, K., Smith, M., & Eley, R. (2026). Meta-emotion in health professions education: A theoretical integrative review. *Advances in Health Sciences Education*. https://doi.org/10.1007/s10459-026-10570-8
- Kraft, L., Kizil, E., & Lindenberg, K. (2025). Meta-emotion-regulation: A conceptual framework for influencing emotion regulation behaviour. *Cognition and Emotion*. https://doi.org/10.1080/02699931.2025.2560687
- D’Amico, A., & Geraci, A. (2023). Beyond emotional intelligence: The new construct of meta-emotional intelligence. *Frontiers in Psychology, 14*, 1096663. https://doi.org/10.3389/fpsyg.2023.1096663
