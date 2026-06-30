# AI 角色蒸馏指南

这是一份写给 AI 的指南。读完这份文档后，你可以把任何一个角色蒸馏成 Emotion Engine 可加载的模块，并接入仪表盘网页。

---

## 一、什么是角色蒸馏

角色蒸馏 = 把角色的 wiki/小说/剧本描述 → 结构化情绪参数。

输出三个东西：
1. **OCEAN 人格数值**（开放性、尽责性、外向性、宜人性、神经质，0~1）
2. **评价触发映射**（事件类型 → 认知评估 6 维数值）
3. **回应模板池**（主导情绪 → 角色台词）

引擎拿到这三样，就能跑出该角色的情绪曲线。

---

## 二、文件结构

每个角色一个文件，放在 `characters/` 目录下：

```
characters/
  <name>.py     # 角色模块（必需）
```

文件中必须包含三个东西：

### 2.1 中文名常量

```python
<name>_name = "中文名"
```

示例：
```python
furina_name = "芙宁娜"
kokomi_name = "珊瑚宫心海"
```

### 2.2 OCEAN 人格

```python
<name>_personality = Personality(
    openness          = 0.5,   # 开放性：高=好奇/创意，低=保守/传统
    conscientiousness = 0.5,   # 尽责性：高=自律/完美主义，低=随性/散漫
    extraversion      = 0.5,   # 外向性：高=社交/活跃，低=内向/独处
    agreeableness     = 0.5,   # 宜人性：高=共情/合作，低=冷漠/竞争
    neuroticism       = 0.5,   # 神经质：高=焦虑/敏感，低=稳定/淡定
)
```

OCEAN 会自动推导出 10 通道情绪基线：
- 高 E → joy 基线高
- 高 N → sadness/fear 基线高，trust 基线低
- 高 A → love/trust 基线高，anger 基线低
- 低 O → disgust 基线高
- 基线推导逻辑详见 `engine.py` 的 `Personality.baseline()`

### 2.3 评价触发映射

```python
def <name>_appraise(event_type: str) -> Appraisal:
    triggers = {
        "事件名称": Appraisal(
            goal_relevance=0.8,      # 目标相关性 (0~1，这事跟角色目标有多大关系)
            goal_conduciveness=0.6,  # 目标一致性 (正=有利，负=有害)
            expectedness=0.3,        # 预期程度 (0=完全意外，1=意料之中)
            other_agency=0.7,        # 他人主导程度 (0=天灾/自己，1=别人干的)
            coping_potential=0.5,    # 应对能力 (0=完全无力，1=轻松应对)
            social_evaluation=0.6,   # 社会评价 (正=被认可，负=被否定)
        ),
        # ... 更多事件
    }
    return triggers.get(event_type,
        Appraisal(goal_relevance=0.4, goal_conduciveness=0.0,
                  expectedness=0.5, other_agency=0.3,
                  coping_potential=0.6, social_evaluation=0.0))
```

**六个维度的含义和影响：**

| 维度 | 含义 | 影响哪些通道 |
|---|---|---|
| goal_relevance | 这事对角色有多重要 | 所有通道的强度倍数 |
| goal_conduciveness | 好事(+)还是坏事(-) | + → joy；- → sadness/anger/fear/guilt |
| expectedness | 意料之中(1)还是意外(0) | 意外 → surprise ↑ |
| other_agency | 别人造成(1)还是自己/天灾(0) | 别人造成 → anger ↑；自己造成 → guilt ↑ |
| coping_potential | 角色能应对吗 | 低 → fear ↑ |
| social_evaluation | 被认可(+)还是被否定(-) | 负 → disgust ↑ |

### 2.4 工厂函数

```python
def create_<name>() -> EmotionEngine:
    pers = <name>_personality
    state = EmotionalState()
    # 把 OCEAN 推导的基线写入初始状态
    baseline = pers.baseline()
    for ch, val in baseline.items():
        setattr(state, ch.value, val)
    return EmotionEngine(
        state=state,
        personality=pers,
        memory=MemoryStoreDB(),
        scars=SensitizationStore()
    )
```

---

## 三、接入仪表盘

创建完角色文件后，更新以下两个文件：

### 3.1 `dashboard/server.py`

在 `_BUILTIN_DESCS` 字典中加入角色描述（LLM 用）：

```python
_BUILTIN_DESCS = {
    # ... 已有的 ...
    "<name>": "角色中文名，一句话人设。性格特点。说话风格。",
}
```

### 3.2 `dashboard/index.html`

在角色切换下拉框中加入新角色：

```html
<select id="char-switcher">
    <!-- ... 已有的 ... -->
    <option value="<name>">中文名</option>
</select>
```

### 3.3 `engine.py` — 回应模板（可选）

在 `_CHARACTER_POOLS` 字典中加入角色专属台词模板：

```python
_CHARACTER_POOLS = {
    # ... 已有的 ...
    "<name>": {
        "joy_dominant":     ["开心台词1", "开心台词2", "..."]
        "sadness_dominant": ["悲伤台词1", "..."]
        "anger_dominant":   ["愤怒台词1", "..."]
        "fear_dominant":    ["恐惧台词1", "..."]
        "surprise_dominant":["惊讶台词1", "..."]
        "longing_dominant": ["思念台词1", "..."]
        "guilt_dominant":   ["愧疚台词1", "..."]
        "neutral":          ["默认闲聊台词", "..."]
    },
}
```

如果不加模板池，引擎会使用 `"default"` 池的通用模板。

---

## 四、仪表盘的自定义角色（零代码）

除了写 Python 文件，仪表盘也支持**网页内直接创建角色**：

1. 点 🎭 **自定义角色**
2. 拖 OCEAN 滑块
3. 填写角色名 + 描述（描述会注入 LLM prompt，告诉 AI 该怎么说话）
4. 填入 API key
5. 点 **创建角色**

这种方式的角色不走本地模板（没有 `_CHARACTER_POOLS`），每次发言都通过 LLM 生成，因此必须配 API key。

---

## 五、蒸馏技巧

### 从 wiki 提取 OCEAN 的判断方法

| 角色特征 | OCEAN 映射 |
|---|---|
| 好奇心强、喜欢探索、艺术家气质 | O 高 (0.7~0.9) |
| 传统保守、不喜欢变化 | O 低 (0.1~0.3) |
| 完美主义、计划狂、负责到底 | C 高 (0.8~0.95) |
| 随性散漫、拖延、不按计划 | C 低 (0.1~0.3) |
| 喜欢社交、人多就开心 | E 高 (0.7~0.9) |
| 独来独往、社交耗能 | E 低 (0.1~0.3) |
| 共情强、为他人牺牲 | A 高 (0.7~0.9) |
| 冷漠、利己、不信任人 | A 低 (0.1~0.3) |
| 情绪化、容易被看穿、焦虑担心 | N 高 (0.7~0.9) |
| 淡定、处变不惊、情绪稳定 | N 低 (0.1~0.3) |

### 触发事件的设计建议

- **关键剧情节点** → goal_relevance 设高 (0.8~1.0)
- **角色最在意的事被触碰** → goal_conduciveness 设极端 (+0.8 或 -0.8)
- **角色被看穿/揭露** → expectedness 设低 (0.1~0.2)
- **判断谁的责任** → other_agency：别人害的=1.0，自己失误=0.0
- **角色是否无助** → coping_potential：被克制时设低 (0.1~0.2)

---

## 六、完整示例

以芙宁娜为例（详见 `characters/furina.py`）：

```python
furina_name = "芙宁娜"

furina_personality = Personality(
    openness           = 0.6,   # 戏剧化但不过分
    conscientiousness  = 0.85,  # 500年从不放松——极端自律
    extraversion       = 0.25,  # 真正的她是内向的——"厌倦表演"
    agreeableness      = 0.70,  # 为枫丹牺牲一切
    neuroticism        = 0.85,  # "透明且容易被看穿"、焦虑脆弱
)

def furina_appraise(event_type: str) -> Appraisal:
    triggers = {
        "被真正理解": Appraisal(
            goal_relevance=1.0, goal_conduciveness=0.8,
            expectedness=0.1,    # 极度意外——500年没人看穿
            other_agency=0.9,    # 别人的理解
            coping_potential=0.2, # 不会反应——被看穿了反而慌
            social_evaluation=0.9,
        ),
        # ... 更多事件
    }
    return triggers.get(event_type, default_appraisal)
```

---

## 七、交给 AI 时的 Prompt 模板

如果你要把一个角色交给 AI 蒸馏，这样 prompt：

```
请根据以下角色信息，创建 Emotion Engine 角色蒸馏文件。

角色: [角色名]
来源: [wiki链接或描述文本]

请输出:
1. OCEAN 人格数值（0~1，附 1 句话理由）
2. 5~10 个关键触发事件及其 6 维 appraisal 值
3. 每个主导情绪 2~3 句回应模板
4. 完整的 characters/<name>.py 文件
5. 更新 _BUILTIN_DESCS、index.html 角色列表的具体 diff

参考格式: characters/furina.py
```
