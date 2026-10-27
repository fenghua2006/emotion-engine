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

## 记忆 v0.2 ✅ — L4 关系域记忆
- 按 `(character_id, user_id)` 隔离的 SQLite 记忆
- 卡片 / 结构化事实 / 关系状态 / 开放循环
- CJK bigram + FTS5 检索；无 embedding 依赖
- 预算打包（cards / facts / relationship / open loops，≤1200 字）
- 规则抽取身份、偏好、边界、承诺
- `appraisal_prior()` → `engine.tick(appraisal_bias=...)`，每维 ±0.2，
  只改 `Appraisal`，不写 `Personality`
- 关系摘要每次成功写入后自动刷新（确定性模板，≤160 字）
- `MemoryWriteGate`：置信度 / 去重 / 冲突 / 开放循环去重 / 轮次预算
- `scripts/migrate_legacy_memory.py`：旧 `emotion.db` 幂等迁移
- `respond_llm()` 可选接入 `memory_context` / `relationship_memory`
- 19 + 27 个 unittest；100 轮 prompt ≤1200 字；有/无记忆消融通过
- 详细设计见 `docs/MEMORY_SYSTEM.md`

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

## 增量开发 L5 ✅ — 耐受校准 v0（校准核心）
- `tolerance.py` 默认四域容量：anger / fear / intimacy / criticism，有效 capacity
  为加权平均；每域独立 clamp 到 `[0.35, 1.80]`
- 伤疤按事件类型定向压缩：`social_hurt` / `criticism` / `cold_shoulder` /
  `self_blame` 映射到不同域，同域取 max，不累加
- `meta_load_bias` 可选消费：`tolerance_meta_load_enabled=False` 默认关闭；
  开启后用上一 tick zone / overload，bias ≤0.2，加在 instant load
- `legacy_tolerance=True`：scalar 容量 + global max scar + 忽略 meta bias，
  逐值复现 pre-L5
- 新增 `l5_events.py`（15 场景 / 41 步）、`l5line.py`、golden 生成脚本；
  `fixtures/l5_tolerance_golden*.json` 双 golden
- `ToleranceConfig` / `ToleranceState` 新字段与序列化；快照保存
  `legacy_tolerance` 与 multi 观测键，配置不持久化
- 38 个 L5 测试；L5.1 追加 12 个；L6 后全量 338 tests OK；L1/L2 四套 golden 无 diff
- calm capacity current 0.862350 / legacy 0.857500；hyper / hypo / 滞回 /
  capacity 上下界 / fuzz 全部锁定
- L5.1 ✅：仪表盘 tolerance 区间带、load/capacity、四域折线；
  hyper/hypo 本地模板、Live2D 与 `_action_tendency` 表达一致性
- 详细设计见 `docs/TOLERANCE.md`

## P0 ✅ — 可维护性护栏
- `tick()` 拆成有序 `_begin_tick` / `_step_*` / `_build_tick_result`；
  调用顺序由 `test_p0_maintainability.py` 锁定
- 表达层移到 `expression.py`（engine 继续 re-export）；LLM 网络调用移到 `llm_client.py`
- 快照 `SNAPSHOT_VERSION` + `_migrate_snapshot_payload()`；记忆库
  `MEMORY_SCHEMA_VERSION` + `PRAGMA user_version` + `_migrate_schema()`
- `pyproject.toml`：ruff（E/F）+ coverage（branch，当前 gate 80%）；
  `.github/workflows/ci.yml`：lint → unittest → coverage → 六套 golden
- 17 个 P0 测试；全量 285 tests OK；六套 golden 无 diff
- 分层、tick 顺序与扩展检查表见 `docs/ARCHITECTURE.md`

## L6 ✅ — 表达 / JEV / Noul / MCP
- `jev.py`：开放文本 → 六维 `Appraisal` + confidence + evidence；
  规则优先，低置信度可选 LLM；有界 LRU 缓存、批量分块、超时与失败回退
- `noul.py`：亲密 / 命令 / 侮辱 / 胁迫 / 威胁五类边界信号；亲密权重随 trust 变化；
  只给建议，最终边界仍由调用方或引擎决定
- `engine.respond_llm(..., use_jev, auto_boundary, trace)`：新增能力默认关闭，
  旧调用逐位不变；失败写 `_jev_error` / `_boundary_error`，主链路继续
- `mcp_server.py`：零依赖 stdio MCP，`tick` / `recall` / `state`；
  可被 Claude Code / Codex 直接调用，记忆按 `(character_id, user_id)` 隔离
- `scripts/replay_trace.py` + 仪表盘 `POST /api/replay`：只读副本回放 StepTrace
- 53 个 L6 测试；全量 338 tests OK；ruff clean；六套 golden 无 diff
- 详细契约见 `docs/JEV.md` / `docs/MCP.md`

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
