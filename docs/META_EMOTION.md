# 元情绪（Meta-Emotion）设计

> 状态：设计已记录，代码未实现；前置 L3 已于 2026-10-26 完成。
> 计划层：**L3.5**（L3 氛围/伤疤之后，L4 记忆之前）。  
> 关联：`docs/CORE.md`、`docs/INCREMENTAL_PLAN.md`、`core.py`、`engine.py`。

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
    belief="anger makes me unlovable",
)
```

L3.5 的 v0 只要求：

- `target`：被指向的 primary channel；
- `kind`：元情绪标签；
- `valence`：−1..1；
- `intensity`：0..1；
- `acceptance`：0..1；
- `control`：0..1；
- `action`：调节意图。

`belief` 由角色档案或未来记忆层提供，不进入第一版公式。

### 4.2 元评估四维

复用 `Appraisal` 的思路，但对象换成“情绪本身”：

| 维度 | 问题 | 高值含义 |
|---|---|---|
| `goal_conduciveness` | 这个情绪对我当前目标有利吗？ | 有用 / 有帮助 |
| `social_acceptability` | 在这个关系、场合里能被接受吗？ | 被允许 / 被认可 |
| `controllability` | 我能不能承受、调节它？ | 可承受 / 可修复 |
| `identity_congruence` | 它符合“我是谁”吗？ | 符合自我 / 角色 |

映射（v0，可调）：

- 低目标 + 低社会接受 + 自责 → 羞耻 / 内疚；
- 高社会接受 + 高身份一致 → 骄傲 / 接纳；
- 低可控 + 高威胁 → 对恐惧的恐惧；
- 高兴趣 + 低威胁 → 好奇 / 接纳。

### 4.3 输出只做有界偏置

元情绪层不直接写 raw channel。它输出：

```python
{
    "meta_emotions": [MetaEmotion, ...],
    "expression_gain": {"anger": 0.6, "fear": 0.3},
    "display_rule": {"anger": "suppress", "joy": "amplify"},
    "regulation_action": {"anger": "suppress"},
    "tolerance_load_bias": 0.08,
    "reappraisal_target": ["social_acceptability"],
}
```

约束：

- 所有增益必须在有界区间内（例如 `0.5..1.5`）；
- 任何会改变曲线的参数必须有开关、默认关闭、前后对比；
- 元情绪不能写 `Personality`，不能改 `baseline()`；
- 元情绪不能直接改 `core.py` 的 `decay()`、`appraise()`、`gate_appraisal()`。

### 4.4 StepTrace 扩展

L3.5 在 `StepTrace` 增加：

| 字段 | 含义 |
|---|---|
| `meta_appraisal` | 对 primary emotion 的四维元评估 |
| `meta_emotions` | 本 tick 激活的元情绪列表 |
| `regulation_bias` | 表达/调节/耐受的有界偏置 |

仍然遵守 L0 契约：`trace=False` 行为不变；新增字段只在 `trace=True` 时出现。

### 4.5 稳定 profile 与瞬时 state 分开

- **MetaEmotionProfile**：角色稳定的元情绪倾向，例如“恐惧→羞耻”“愤怒→骄傲”“快乐→内疚”。放在角色档案里，不写 `Personality`。
- **MetaEmotionState**：当前事件触发的瞬时元情绪。

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
| L1 | 六条快通道保持纯净；golden 测试不引入元情绪 |
| L2 | 提供 guilt / trust / love，元情绪依赖这些慢通道与自我归因 |
| L3 | ✅ 修正氛围/伤疤顺序；元情绪读取稳定的 atmosphere / scar 状态 |
| **L3.5** | **元情绪层：二阶评估 + 调节意图 + 有界偏置** |
| L4 | 记忆可提供元情绪信念与关系规范；元情绪不直接写记忆 |
| L5 | 耐受可消费 `tolerance_load_bias`；不重复计算同一负荷 |
| L6 | 表达层根据 `display_rule` 选择措辞/表情；LLM 只表达，不生成元情绪 |

## 6. L3.5 范围与验收

### 目标

让引擎能够回答：**“角色对这个情绪本身是什么态度？”**  
而不是只回答：**“角色现在有多愤怒？”**

### 工作项

1. 定义 `MetaEmotion`、`MetaAppraisal`、`MetaEmotionProfile` 数据结构。
2. 实现 `meta_appraise()`：primary channel → 四维元评估 → 元情绪标签。
3. 实现 `meta_regulate()`：元情绪 → 有界表达/调节/耐受偏置。
4. 接入 `engine.tick(trace=True)`，默认关闭；开启后只在 `trace` 中产生字段，不改变旧曲线。
5. 增加 `StepTrace.meta_appraisal` / `meta_emotions` / `regulation_bias`。
6. 在 `characters/*.py` 增加可选的 `meta_emotion_profile`。
7. 对照测试：同一事件序列，开启/关闭元情绪层的曲线差异。
8. 至少一个角色 demo 展示“愤怒→羞耻→压抑”与“愤怒→骄傲→表达”的差异。

### 验收

- 关闭元情绪层时，L1/L2 四套 golden 不变，L3 corrected / legacy A/B 场景逐值不变。
- 开启后，基础通道值不被直接改写；只有表达/调节/耐受偏置变化。
- 元情绪不写 `Personality`。
- 同一 `target` 的元情绪可以同时存在（例如“羞耻 + 兴趣”），且不互相抵消。
- 连续 20 个事件无 NaN / Inf / 越界。
- 至少一个角色 demo 退出码 0。

### 不做

- 不新增基础情绪通道；
- 不接 JEV / LLM 生成元情绪；
- 不改 tolerance 参数；
- 不把元情绪写进 `core.py` 的核心公式；
- 不在 L1/L2 阶段提前实现。

## 7. 开放问题

1. `acceptance` 与 `control` 是否独立？可能高度相关，需用 golden set 检查。
2. 元情绪是否只对“显著高于基线”的通道激活？阈值是多少？
3. 元情绪标签用离散 `kind`，还是三维连续向量（valence / acceptance / control）？建议 v0 两者并存，离散标签用于表达，连续值用于计算。
4. 元情绪是否影响下一 tick 的 appraisal 增益？建议先只影响表达与耐受，避免污染基础曲线。
5. 元情绪与“伤疤”的区别：伤疤是事件记忆的放大，元情绪是对当前情绪的态度；两者可以叠加，但不能重复计负荷。
6. 角色档案中的元情绪信念是否应由 LLM 蒸馏生成？第一版手工配置，避免不可解释。

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
