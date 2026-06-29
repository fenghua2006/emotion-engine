# Emotion Engine

多通道情绪交互引擎。10 个独立情绪通道，无硬上限，弹性衰减，论文验证——为 AI 伴侣/NPC/角色扮演提供可观测的情绪动力学。

## 为什么不用 PAD

PAD（Pleasure-Arousal-Dominance, 1974）把情绪压缩成三维坐标中的单点。真实情绪不是这样的——

> 你可以同时感到愤怒和悲伤。它们不会互相抵消。它们互相调制。

Emotion Engine 给每种情绪一个独立通道。joy=0.6 + sadness=0.5 同时存在，这是"苦涩的微笑"，不是一个坐标点。50 年后，该有人替代它了。

## v0.4 架构

```
                    ┌──────────────┐
                    │   Atmosphere  │  -1 relaxed ~ +1 tense
                    │   氛围修饰器   │  调制所有 appraisal
                    └──────┬───────┘
                           │
    ┌──────────────────────┼──────────────────────┐
    │                      │                      │
    ▼                      ▼                      ▼
┌────────┐          ┌────────────┐         ┌──────────────┐
│  Fast  │◄─────────│   Slow     │         │   Absence    │
│  6 ch  │  gate    │  trust     │  couple │  longing     │
│ joy    │◄─────────│  love      │────────►│  guilt       │
│ sad    │   modulate│           │         │              │
│ anger  │          └────────────┘         └──────────────┘
│ fear   │
│ disgust│          ┌────────────┐
│surprise│          │ Interaction│  情绪间不抵消，互相调制
└───┬────┘          │   Matrix   │
    │               └────────────┘
    ▼
┌─────────┐    ┌──────────┐    ┌──────────────┐
│ Elastic │───►│  Memory  │───►│ Sensitization │
│ Decay   │    │  Arousal │    │ Kindling +    │
│无硬上限  │    │  固化窗口 │    │ Bayesian 威胁  │
└─────────┘    └──────────┘    └──────────────┘
```

## 快速开始

```bash
git clone https://github.com/fenghua2006/emotion-engine
cd emotion-engine
uv run python engine.py
uv run python characters/kokomi.py
```

## 基本用法

```python
from engine import (
    EmotionEngine, EmotionalState, Personality, Appraisal,
    MemoryStore, SensitizationStore
)

pers = Personality(extraversion=0.7, neuroticism=0.6, agreeableness=0.7)
state = EmotionalState()
engine = EmotionEngine(
    state=state, personality=pers,
    memory=MemoryStore(), scars=SensitizationStore()
)

result = engine.tick(Appraisal(
    goal_relevance=0.8, goal_conduciveness=-0.7,
    expectedness=0.2, other_agency=0.9,
    coping_potential=0.2, social_evaluation=-0.3
))
# result["state"] → 10 通道原始值
# result["felt"]  → log 压缩感知值
# result["memory"] → 记忆统计
# result["atmosphere"] → 氛围值
# result["scars"] → 旧伤疤列表（trigger sensitization）
```

## 10 通道

| 通道 | 类型 | 触发 | 半衰期 |
|---|---|---|---|
| joy | 快 | 好事发生 | 90min |
| sadness | 快 | 坏事发生 | 180min |
| anger | 快 | 别人造成的伤害 | 120min |
| fear | 快 | 不可控的威胁 | 60min |
| disgust | 快 | 负面社会评价 | 90min |
| surprise | 快 | 意外事件 | 30min |
| **trust** | **慢** | 正面交互累积，背叛腰斩，被 love 引力线牵引 | 不衰减 |
| **love** | **慢** | 时间+正面交互累积，信任缺口大时被侵蚀 | 不衰减 |
| **longing** | **缺失** | 离线时 love × log(时间) 生长，在线时消退 | 半小时 |
| **guilt** | **自责** | self_agency > 0.3 且造成伤害 | 180min |

## 关键机制

### 弹性衰减
```
effective_T½ = T½ / (1 + |value - baseline| / baseline)
```
偏离基线越远，回弹越快。无硬上限——用自然饱和 + log₁₀ 感知压缩替代。

### 双时钟
```
在线 tick(): 逐帧处理事件
离线 wake(): 休眠后一次性结算，各通道独立压缩
  anger ×0.08 — 睡醒基本不气
  sadness ×0.30 — 悲伤穿透睡眠
  love ×0.98 — 几乎不打折
```

### 记忆-情绪耦合
- **Arousal 驱动存储**（非 Valence）— Diamond 2007 / Bowen 2016
- **30 分钟固化窗口** — Nielson 2007
- **Arousal 标记召回**（非情绪一致性）— York 2025
- **Saga 效应** — 长期记忆每 24h 微拉基线

### 触发敏感化（旧伤疤）
- **Kindling** — 同模式 3 次后门槛降 8%/次，最敏感 +60% — Post 1992
- **贝叶斯威胁** — 负面信号 5× 权重，需 30 次正面才复位 — MindLAB 2024
- **去情境化恐惧** — 原始模式泛化到相似信号 — Neudert 2024

## 角色蒸馏

`characters/kokomi.py` — 珊瑚宫心海（高尽责·社交焦虑的军师）
`characters/columbina.py` — 哥伦比娅（极内向·孤独清冷的月神）

wiki → OCEAN → baseline → appraisal trigger 映射。
两个角色在同一引擎上跑出完全不同的曲线——验证了引擎的区分度。

## 论文基础

| 决策 | 论文 |
|---|---|
| 并行匹配流替代 PAD | Jennings (2025) |
| 混合情绪互不消灭 | Vanderbilt Emotional Blends |
| Arousal 驱动记忆固化 | Diamond (2007) / Bowen (2016) |
| 30min 固化窗口 | Nielson (2007) |
| 人格 30 岁后稳定 | Ones, Stanek & Dilchert (2024) |
| 人格三层模型 | McAdams |
| Kindling 敏感化 | Post (1992) / Kendler (2000) |
| 贝叶斯威胁模型 | MindLAB Neuroscience (2024) |
| 抑郁现实主义 | Sci Reports (2024) |

## License

MIT · 枫骅 2026
