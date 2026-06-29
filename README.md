# Emotion Engine

多通道情绪交互引擎。8 个独立情绪通道同时存在，无硬上限，弹性衰减——为 AI 伴侣/NPC/角色扮演提供可观测的情绪动力学。

## 为什么不用 PAD

PAD（Pleasure-Arousal-Dominance）把情绪压缩成三维坐标中的单点。真实情绪不是这样的——

> 你可以同时感到愤怒和悲伤。它们不会互相抵消。它们互相调制。

Emotion Engine 给每种情绪一个独立通道。喜悦和悲伤可以同时为 0.6，这是"苦涩的微笑"，不是一个坐标点。

## 核心设计

```
慢通道(trust, love) ──累积调制──→ 快通道(joy, sad, anger, fear, disgust, surprise)
                                        │
                                   交互矩阵 ←── 情绪间不抵消，互相调制
                                        │
                                   弹性衰减 ←── 偏离基线越远，回弹越快
                                        │
                                   感知压缩 ←── raw → log映射，边际递减
```

## 快速开始

```bash
git clone https://github.com/fenghua2006/emotion-engine
cd emotion-engine

# 跑内置测试
uv run python engine.py

# 跑心海角色测试
uv run python characters/kokomi.py
```

## 基本用法

```python
from engine import EmotionEngine, EmotionalState, Personality, Appraisal

# 定义人格（OCEAN → 基线情绪）
pers = Personality(
    extraversion=0.7,   # 外向
    neuroticism=0.6,    # 情绪不稳定
    agreeableness=0.7,  # 随和
    openness=0.8        # 开放
)

state = EmotionalState()
engine = EmotionEngine(state=state, personality=pers)

# 一个事件来了
result = engine.tick(Appraisal(
    goal_relevance=0.8,         # 跟我多相关
    goal_conduciveness=-0.7,    # 帮我(+)还是挡我(-)
    expectedness=0.2,           # 意外程度(低=意外)
    other_agency=0.9,           # 他人因素占比
    coping_potential=0.2,       # 我能控制多少
    social_evaluation=-0.3      # 社会评价
))

# result["state"]  → 8通道原始值
# result["felt"]   → log压缩后的感知值
# result["blends"] → 触发的混合情绪标签
# result["shock_channels"] → 冲击通道
```

## 通道表

| 通道 | 类型 | 说明 |
|---|---|---|
| joy | 快 | 事件冲击 ↑, 弹性衰减 ↓ |
| sadness | 快 | 消极留存更久(半衰期 180min) |
| anger | 快 | 受 trust 门控 |
| fear | 快 | 受 trust 门控，受 love 调制 |
| disgust | 快 | 道德愤怒放大器 |
| surprise | 快 | 消散最快(30min) |
| **trust** | **慢** | 正面交互累积 ↑, 背叛腰斩 ↓, 不自动衰减 |
| **love** | **慢** | 时间+正面交互累积 ↑, 极慢变化 |

## 关键机制

### 弹性衰减
```
effective_T½ = T½ / (1 + distance)
```
偏离基线 2× → 衰减加速 3×。极端情绪恢复快，近基线情绪平稳。

### 感知压缩
```
felt = log₁₀(1 + raw × 2)
```
raw 从 1.0 → 2.0 的感知差 ≈ raw 从 0.1 → 0.3 的感知差。无天花板，但边际递减。

### 对比度冲击
```
shock = e^(Δ × 2) - 1  (clamped at Δ=3)
```
相同绝对值，陡变比稳态更痛。冲击感决定记忆存储优先级。

## 角色蒸馏

见 `characters/kokomi.py` —— 从角色 wiki 蒸馏出 OCEAN 人格 + 触发映射。

```
wiki → OCEAN → baseline → triggers[] → engine.tick()
```

## 理论来源

- Jennings (2025): "A Computational Model of Human Emotion" — 情绪 = 多个并行匹配流
- Vanderbilt Emotional Blends — 混合情绪中各组分互不消灭
- Self-Excited Dynamics (2024) — 消极情绪留存更久
- Sentipolis (CMU, 2025) — 情绪-记忆耦合

## License

MIT
