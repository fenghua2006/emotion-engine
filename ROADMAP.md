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
- LLM 双模式（本地模板 / DeepSeek + Kimi 保底）
- 角色专属模板池（每人独立 _CHARACTER_POOLS）
- Live2D 桥（VTube Studio API 驱动，表情+面部参数）
- 低信任 disgust 检测（love bombing 感知）

## v0.6 — 可视化 + 产品化
- 实时仪表盘（HTTP 服务 + HTML 面板，任意角色挂载）
- 情绪折线历史 + 氛围仪表 + 伤疤标记
- 网页内热切换角色（furina/kokomi/columbina/default）
- GPT-SoVITS v2ProPlus 芙宁娜声线（权重已下，API 待调通）
- 情绪驱动语音参数（speed/pitch 随引擎通道变化）
- 桌面双击即用的 furina_bridge.py

## 记忆 v0.1 ✅ — 关系域长期记忆
- 按 `(character_id, user_id)` 隔离的 SQLite 记忆
- 卡片 / 结构化事实 / 关系状态 / 开放循环
- CJK bigram + FTS5 检索；无 embedding 依赖
- 预算打包（cards / facts / relationship / open loops，≤1200 字）
- 规则抽取身份、偏好、边界、承诺
- `respond_llm()` 可选接入 `memory_context` / `relationship_memory`
- 19 个 unittest；详细设计见 `docs/MEMORY_SYSTEM.md`

## 耐受空间 v0.7 ✅ — 忍耐空间
- `tolerance.py`：load / capacity / regulated-hyper-hypo
- 负荷只取负性通道相对基线的偏离；trust/love 扩展容量
- 两 tick 滞回；hyper 放大负性输入并拖慢恢复，hypo 降低反应
- `tick()` / `wake()` 注入时钟；快照完整持久化
- 22 个 unittest（14 tolerance + 8 engine integration）
- 详细设计见 `docs/TOLERANCE.md`

## 增量开发 L0 ✅ — 核心情绪线
- `core.py`：通道 / 衰减 / 六维评估 / 门控 / 交互 / 慢通道，不依赖上层
- `engine.py` 保留组合根与旧 import 路径
- `tick(trace=True)` 返回 `StepTrace` 分阶段快照，不改变计算
- 28 个 core 测试；旧 41 个测试全部通过（合计 69）
- 记录两个顺序问题：氛围门控写入过晚、shock 密度采样过早（L3 已修，legacy 保留）
- 详细契约见 `docs/CORE.md`；完整 L0–L6 安排见 `docs/INCREMENTAL_PLAN.md`

## 增量开发 L1 ✅ — 六条快通道
- `fastline.py`：固定人格 / 固定 trust-love / 固定时钟的六通道参考线
- 46 条冻结事件 + 双 golden（当前 + v0.7 legacy）
- 22 个 L1 测试 + 11 个交互测试，golden 误差 < `1e-9`；L1 阶段全量 102 tests OK
- 详细设计见 `docs/L1_FAST_CHANNELS.md`

## 增量开发 L1.1 ✅ — 交互激活修复
- 根因：交互矩阵用绝对通道值 0.05 做门槛，surprise / disgust 基线被当成激活
- 修复：基线相对激活 + 快通道目标共激活；`legacy_interactions=True` 保留旧行为
- 空闲 300 tick：joy 0.4220 → 0.3500，fear 0.1920 → 0.1050，anger 0.1239 → 0.0550
- 纯负面事件 joy：0.4119 → 0.3500；正面事件不再获得无来源 fear / anger
- 剩余已知问题：dominant 基线偏差
- 对照数据见 `docs/L1_FAST_CHANNELS.md` §5

## 增量开发 L2 ✅ — 慢通道与缺失通道
- `slowline.py`：固定人格 / 固定时钟的十通道参考线（在线 tick + 离线 wake）
- 19 条冻结事件 + 双 golden（当前 L2 + 旧慢通道 / 绝对阈值对照）
- 32 个 L2 测试；四套 golden 误差 < `1e-9`；全量 134 tests OK
- trust 交互基线 = `trust_target(love)`，love / 快通道 / guilt = 人格基线
- guilt 在 `self_agency ≤ 0.3` 时严格为 0；外部归因不产生愧疚
- love 正面事件改为按区间输入；trust / love 数值护栏与 trust 目标封顶
- 30 天模拟 trust 0.872741 = target，love 1.959433，不发散
- `legacy_slow_channels` + `legacy_interactions` 保留旧曲线
- 详细设计见 `docs/L2_SLOW_CHANNELS.md`

## 增量开发 L3 ✅ — 氛围与伤疤
- 新增 `atmosphere.py`：目标氛围、单步漂移、clamp、氛围调制、伤疤放大上限
- 默认 corrected：氛围在 state 写入前调制 gated；shock 检测后再采样密度，下一 tick 生效
- corrected atmosphere ∈ `[-1, 1]`；伤疤只放大 sadness / fear / anger，≤60%
- `legacy_atmosphere=True` 逐字保留 pre-L3 顺序、零密度与旧 trace notes
- `StepTrace` 增加 atmosphere / scar / gated_written 观测字段
- 32 个 L3 测试；全量 166 tests OK；L1/L2 四套 golden 逐位未变
- 单次严重事件：corrected atmosphere -0.200000 / density 0.714300；legacy 精确兼容
- 不设 L3 golden；契约由纯函数测试 + A/B + 一条 pre-L3 精确值锁定
- 详细设计见 `docs/L3_ATMOSPHERE_SCARS.md`

## 增量开发 L3.5 ✅ — 元情绪 v0（trace-only）
- 新增纯模块 `meta_emotion.py`：五个 frozen dataclass + `meta_appraise()` /
  `meta_regulate()` / `evaluate_meta_emotion()`
- 元情绪是二阶过程，不是第 11 个基础通道；`target` 指向 primary channel
- `kind`：shame / guilt / pride / contempt / compassion / interest / fear；
  `action`：accept / suppress / ruminate / express / repair / avoid
- 元评估四维：goal / social / control / identity；双阈值激活（felt 0.04 + raw 0.03）
- `StepTrace` 开启时追加 `meta_enabled / meta_appraisal / meta_emotions /
  regulation_bias`；关闭时仍是原 27 键
- 默认中性：无 profile 时不预设“愤怒→羞耻”；`furina_meta_shame_profile` /
  `furina_meta_pride_profile` 展示同一 anger 的两种二阶态度
- v0 只观测：bias 不进入 `respond()`、tolerance 或下一 tick appraisal
- 25 个 L3.5 测试；全量 191 tests OK；四套 golden 与 L3 A/B 逐值不变
- demo：`characters/meta_emotion_demo.py` → shame 0.585 / suppress vs pride 0.340 / express
- 设计根因、公式与验收见 `docs/META_EMOTION.md`

## v0.7 — MCP 封装（进行中）
- Claude Code / Codex 直接调引擎
- /emotion tick / /emotion recall
- 记忆系统接入 appraisal / 自动关系摘要
- 仪表盘展示 tolerance 区间带

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
