# Roadmap

## v0.1 ✅
8通道引擎、快慢双速、交互矩阵、认知评估

## v0.2 ✅
无硬上限、弹性衰减、log₁₀ 感知压缩、心海蒸馏

## v0.3 ✅ — 记忆·双时钟
- 记忆-情绪耦合（Arousal 驱动 + 30min 固化窗口 + 三级记忆）
- Saga 效应（长期记忆每 24h 拉基线）
- 双时钟（在线 tick + 离线 wake，各通道独立压缩）
- 抑郁现实主义（高 sadness 回忆更准）

## v0.4 ✅ — 经验层
- 思念 longing（缺失驱动——离线生长，在线消退）
- 愧疚 guilt（自责驱动——self_agency > 0.3 激活）
- 氛围修饰器（-1 轻松 ~ +1 紧绷，调制 appraisal）
- trust-love 耦合对（互相照应，不是固定基线）
- 触发敏感化（Kindling + 贝叶斯威胁模型，旧伤疤放大负面）
- 10 个通道
- 3 个蒸馏角色：心海、哥伦比娅、芙宁娜

## v0.5 ✅ — 持久化 + 本地思维
- SQLite 持久化（记忆/状态/伤疤断线不丢）
- 本地 respond() —— 零 token 回应系统
- LLM 双模式（本地模板 / DeepSeek API 自由切换）
- 角色专属语音包（每人独立模板池）
- Live2D 桥（VTube Studio API 驱动，表情+面部参数）
- 低信任 disgust 检测（love bombing 感知）

## v0.6 — 语音克隆 + 产品化
- GPT-SoVITS v2ProPlus 芙宁娜声线（权重已下，API 待调通）
- 情绪驱动语音参数（speed/pitch 随引擎通道变化）
- 录屏+OBS 出片流程
- 桌面双击即用的 furina_bridge.py

## v0.7 — MCP 封装
- Claude Code / Codex 直接调引擎
- /emotion tick / /emotion recall

## v0.8 — 多角色交互
- 两个 EmotionEngine 实例间的情绪感染
- A 的 appraisal → B 的情绪输入

## v0.9 — 角色市场
- 社区贡献蒸馏配方
- 一个 JSON = 一个角色

## v1.0 — 稳定发布
- pip install emotion-engine
- Colab notebook
- 完整英文文档
- B站宣传视频
