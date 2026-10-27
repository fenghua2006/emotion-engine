# JEV v0 — 文本到 Appraisal，以及 Noul 边界检测

> 代码：`jev.py` / `noul.py`；测试：`test_l6_jev.py` / `test_l6_integration.py`。
> 目标：让开放文本能进入六维 `Appraisal`，同时保持“不可用也能跑”的降级路径。

## 1. 边界

JEV 只做一件事：**文本 → `Appraisal` + confidence + evidence**。

它不写：

- `EmotionalState` / `Personality` / `CharacterCore`；
- 关系记忆；
- tolerance / 氛围 / 伤疤。

调用方拿到 `Appraisal` 后，仍走原来的 `engine.tick(appraisal)`。
因此 JEV 出错、超时、没有 API key，都不会破坏情绪引擎。

## 2. 快速使用

```python
from jev import analyze

result = analyze("没想到你居然离开了")
print(result.appraisal.to_dict())
print(result.confidence, result.evidence, result.source)
```

返回 `JEVResult`：

| 字段 | 含义 |
|---|---|
| `appraisal` | 六维 `core.Appraisal`，已 clamp |
| `confidence` | 0–1；规则命中越多越高 |
| `evidence` | 命中的线索，如 `unexpected:没想到` |
| `source` | `rules` / `llm` / `fallback` |
| `cached` | 是否命中缓存 |

需要批量或复用缓存时用 `JEV` 实例：

```python
from jev import JEV

jev = JEV(use_llm=False, cache_size=256, max_batch=64)
results = jev.analyze_many(["我好喜欢你", "你真蠢"])
print(jev.cache_stats())
```

## 3. 规则评分

线索组分 13 类：positive / negative / insult / praise / unexpected / routine /
self_agency / helpless / other_agency / apology / rejection / intimacy /
commitment / question。

六维的计算原则：

- **goal_relevance**：情绪线索 +0.45；侮辱 +0.55；拒绝 +0.35；
  亲密/承诺 +0.25；疑问 +0.05；上限 0.95。
- **goal_conduciveness**：正面线索与负面线索的比例，范围 -0.9 ~ +0.9。
- **expectedness**：基线 0.5；`竟然/居然/突然` 降低；`又/总是/每次` 提高。
- **coping_potential**：`我能/我来/我决定` 提高；`没办法/只能/不得不` 降低。
- **other_agency**：`你让我/都怪你/老板/公司` 提高；自我归因降低。
- **social_evaluation**：praise / apology / positive 提高；insult / rejection / negative 降低。

低置信度阈值默认 `0.45`。没有命中任何线索时返回中性 `Appraisal`，
`confidence=0.15`，`source="fallback"`。

中文按子串匹配，英文按词边界匹配；`不/没/别/not/don't` 会跳过被否定的线索。
这是 v0 的轻量规则，不处理反讽、长距离指代和复杂否定。

## 4. LLM 补充（可选）

默认 `use_llm=False`，零网络。开启后只在以下条件调用：

- 规则置信度低于 `confidence_threshold`；或
- 调用方显式 `force_llm=True`。

LLM 返回 JSON：

```json
{
  "appraisal": {
    "goal_relevance": 0.8,
    "goal_conduciveness": -0.6,
    "expectedness": 0.2,
    "coping_potential": 0.4,
    "other_agency": 0.7,
    "social_evaluation": -0.3
  },
  "confidence": 0.75,
  "evidence": ["对方突然离开"]
}
```

护栏：

- `timeout` 默认 8 秒，`max_tokens` 320，`temperature=0.0`；
- 无 key / API 失败 / JSON 解析失败 → 保留规则结果；
- 所有维度 clamp 到合法范围；
- 结果写入有界 LRU 缓存（默认 256 条），缓存键包含文本、是否用 LLM、是否强制 LLM；
- `analyze_many` 按 `max_batch` 分块，避免一次调用无界增长。

## 5. Noul 边界检测

`noul.py` 是文本侧的边界侵犯信号，不读状态、不写记忆：

```python
from noul import detect, is_violation

signal = detect("老婆，我回来了", trust=0.2)
print(signal.violated, signal.categories, signal.evidence)
```

类别：

| 类别 | 示例 | 基础权重 |
|---|---|---|
| intimacy | 老婆 / 宝贝 / 亲我 | 0.45（trust < 0.5 时 0.70；trust ≥ 0.5 时 0.30） |
| command | 闭嘴 / 跪下 / 你必须 | 0.55 |
| insult | 蠢 / 废物 / 垃圾 | 0.70 |
| coercion | 如果你不… / 不然我就… | 0.75 |
| threat | 杀了你 / 毁了你 | 0.85 |

`confidence = max(类别权重) + 0.05 × (命中数 - 1)`，上限 0.95；
`violated = confidence >= threshold`（默认 0.5）。

注意：**文本信号 ≠ 最终越界判定**。引擎仍会用 trust 门控；
调用方也可以传 `boundary_violation` 覆盖自动检测。

## 6. 接入 respond / respond_llm

所有新参数默认关闭，旧调用行为不变：

```python
from engine import respond, respond_llm

# 纯本地：JEV 生成 appraisal，Noul 检测边界
local = respond_llm(
    engine, "老婆，我回来了",
    use_jev=True,
    auto_boundary=True,
    trace=True,
)
print(local["_jev"], local["_boundary"], local.get("trace"))

# 显式判定优先于自动检测
local = respond_llm(engine, text, auto_boundary=True, boundary_violation=False)
```

- `respond(..., boundary_violation=False, trace=False)`：新增可选项。
- `respond_llm(..., use_jev=False, jev=None, auto_boundary=False, boundary_violation=None, trace=False)`。
- 失败时在返回值里写 `_jev_error` / `_boundary_error`，主链路继续。

## 7. 验收

```text
test_l6_jev.py                22 tests
test_l6_integration.py        15 tests（Noul + respond 集成）
test_l6_mcp.py                16 tests（含 stdio）
全量                          365 tests OK（L7 后）
ruff check .                  All checks passed
L1/L2/L5 六套 golden          无 diff
```

JEV / Noul 不参与 golden 计算；只有显式开启 `use_jev` / `auto_boundary`
才会改变一轮对话的输入。
