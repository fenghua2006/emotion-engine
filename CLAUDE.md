# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

多通道情绪交互引擎 — a 10-channel emotion engine for AI companions. Replaces the 1974 PAD (Pleasure-Arousal-Dominance) model with independent emotion channels that modulate each other without canceling. MIT license. Pure dynamics live in `core.py`; `engine.py` is the composition root (~1,725 lines) plus `atmosphere.py`, `meta_emotion.py`, `tolerance.py`, `memory.py`, `expression.py`, `llm_client.py`, `jev.py`, `noul.py`, and `contagion.py` modules.

Owner: 枫骅 (fenghua2006). GitHub: `github.com/fenghua2006/emotion-engine`.

## Working copy

- **Primary development copy (all new work):** `C:\Users\Administrator\Desktop\emotion-engine`
- **Frozen snapshot:** `D:\Users\Administrator\Desktop\emotion-engine` (2026-10-25; do not edit or sync back)
- Run every command below from the C: copy.

## Running

```bash
cd C:\Users\Administrator\Desktop\emotion-engine

# Core engine demo
uv run python engine.py

# Character tests (3 distilled characters)
uv run python characters/furina.py
uv run python characters/kokomi.py
uv run python characters/columbina.py

# L3.5 meta-emotion demo (shame/suppress vs pride/express)
uv run python characters/meta_emotion_demo.py

# Scenario test
uv run python test_iloveyou.py

# Tests (stdlib unittest; no pytest dependency)
python -m unittest test_core_line.py test_tolerance.py test_engine_tolerance.py -v
python -m unittest test_memory_system.py test_l4_memory_integration.py -v
python -m unittest test_l1_fast_channels.py test_interaction_activation.py -v
python -m unittest test_l2_slow_channels.py -v
python -m unittest test_l3_atmosphere_scars.py -v
python -m unittest test_l3_5_meta_emotion.py -v
python -m unittest test_l5_tolerance.py test_l5_1_expression_dashboard.py -v
python -m unittest test_p0_maintainability.py -v
python -m unittest test_l6_jev.py test_l6_integration.py test_l6_mcp.py -v
python -m unittest test_l7_contagion.py test_l7_integration.py -v
python -m unittest test_l7_1_bidirectional.py test_l7_2_relationship_model.py -v
python -m unittest test_l7_3_network.py test_l7_4_directional.py -v

# Full suite (436 tests)
python -m unittest discover

# L6 JEV / MCP
uv run python mcp_server.py --db memory.db
uv run python scripts/replay_trace.py --text "没想到你居然离开了" --character furina

# L7 multi-character contagion demo
uv run python scripts/multi_character_demo.py
uv run python scripts/multi_character_demo.py --gain 0.3 --cap 0.1
uv run python scripts/multi_character_demo.py --bidirectional --strength 0.5
uv run python scripts/multi_character_demo.py --auto-strength --bidirectional
uv run python scripts/contagion_network_demo.py

# L1 golden generation / verification
python scripts/generate_l1_golden.py                    # current behavior
python scripts/generate_l1_golden.py --check
python scripts/generate_l1_golden.py --legacy           # v0.7 comparison
python scripts/generate_l1_golden.py --legacy --check

# L2 golden generation / verification
python scripts/generate_l2_golden.py                    # current L2 behavior
python scripts/generate_l2_golden.py --check
python scripts/generate_l2_golden.py --legacy           # old slow channels + absolute interactions
python scripts/generate_l2_golden.py --legacy --check

# L5 tolerance golden generation / verification
python scripts/generate_l5_tolerance_golden.py          # current multi-domain behavior
python scripts/generate_l5_tolerance_golden.py --check
python scripts/generate_l5_tolerance_golden.py --legacy # pre-L5 scalar / global-max scar
python scripts/generate_l5_tolerance_golden.py --legacy --check

# Dashboard (real-time visualization)
uv run python dashboard/server.py                  # default generic character
uv run python dashboard/server.py --char furina    # specific character
uv run python dashboard/server.py --char kokomi
uv run python dashboard/server.py --port 8080      # custom port
# → browser: http://localhost:9020
```

### Non-repo scripts (Desktop)

```bash
uv run python C:\Users\Administrator\Desktop\furina_bridge.py   # Live2D + TTS bridge
uv run python C:\Users\Administrator\Desktop\furina_fresh.py    # Baseline + scenario test
```

## Architecture

Core dynamics: `core.py` (no memory/tolerance/LLM imports). Atmosphere/scar pure transforms: `atmosphere.py`. Meta-emotion pure layer: `meta_emotion.py`. Composition root: `engine.py` (~1,725 lines). Control layer: `tolerance.py`. Relationship memory: `memory.py`. Presentation: `expression.py` + `llm_client.py`. Text entry: `jev.py` + `noul.py`. Multi-character contagion: `contagion.py`. MCP wrapper: `mcp_server.py`. Golden/reference harnesses: `fastline.py` + `l1_events.py`, `slowline.py` + `l2_events.py`, `l5_events.py` + `l5line.py`. No package structure yet. `characters/` holds per-character distillations. `dashboard/` holds HTTP server + HTML panel.

### L0 boundary

`core.py` owns channels, `EmotionalState`, decay, appraisal, gating, interaction matrix, slow-channel growth, and `StepTrace`. It must not import `engine`, `atmosphere`, `tolerance`, `memory`, SQLite, or network code. `engine.py` re-exports the old names so existing imports keep working.

`tick(..., trace=True)` adds `result["trace"]` with phase snapshots. Corrected order: `before`, `after_decay`, `after_slow_channels`, `after_appraisal`, `after_interaction`, `after_atmosphere`, `final`. Legacy atmosphere order is `after_appraisal`, `after_atmosphere`, `after_interaction`, `final`. `trace=False` must remain behavior-compatible. New layers should read the trace and supply bounded biases rather than rewriting core formulas. Contracts: `docs/CORE.md` and `docs/INCREMENTAL_PLAN.md`.

### L1 boundary

`fastline.py` is the six-fast-channel reference line: fixed OCEAN=0.5, fixed `trust=love=0.5`, fixed 10-minute steps, no tolerance / scars / atmosphere / memory. `l1_events.py` freezes 46 events. Two golden sets exist: `fixtures/l1_golden.json` (current, baseline-relative interactions) and `fixtures/l1_golden_legacy.json` (v0.7 absolute-threshold interactions). `test_l1_fast_channels.py` has 22 tests; `test_interaction_activation.py` has 11. Regenerate golden only for an intentional behavior change, and review every diff.

L1.1 (2026-10-26) fixed the idle interaction floor and co-activation semantics: `apply_interactions(..., baseline=...)` now uses activation above `Personality.baseline()`, and fast-channel amplification requires the target to be active too. `EmotionEngine.legacy_interactions=True` restores old behavior. Remaining known issue: `dominant` uses “highest raw value > 0.3”, so joy baseline 0.35 biases the label. Details: `docs/L1_FAST_CHANNELS.md`.

### L2 boundary

`slowline.py` is the ten-channel L2 reference line: fixed personality and clock, online through the `tick()` slow-channel path and offline through `wake()`. `l2_events.py` freezes 19 events. Two golden sets: `fixtures/l2_golden.json` (current L2) and `fixtures/l2_golden_legacy.json` (old slow channels + absolute-threshold interactions). `test_l2_slow_channels.py` has 32 tests.

L2 (2026-10-26) fixed four semantics:

- **Interaction baselines:** trust uses `trust_target(love)`; love, the six fast channels, and guilt use personality baselines. Using personality baseline for trust made normal relationship trust suppress fear on idle ticks (fear 0.105 → 0.0 over 300 ticks). `gate_appraisal()` keeps the absolute trust buffer; the interaction matrix only handles trust above relationship equilibrium.
- **Guilt gate:** `self_agency ≤ 0.3` yields exactly zero; above 0.3 scales smoothly. `legacy_slow_channels=True` restores the old threshold-free behavior.
- **Positive-event input:** `grow_slow_channels(..., positive_count, ...)` expects new positive events in the interval, not lifetime cumulative count. The engine passes 0/1 per tick; legacy passes cumulative `positive_events`.
- **Numerical guards:** trust clamped to [0, 1], love to [0, 2]; `love → trust` and `guilt → trust` gains share a cumulative `trust_ceiling` at `trust_target(love)`.

`legacy_slow_channels=True` (old guilt/cumulative/slow baseline) plus `legacy_interactions=True` (absolute interactions) approximates pre-L1.1 behavior. Snapshots persist both flags. Details: `docs/L2_SLOW_CHANNELS.md`.

### L3 boundary

`atmosphere.py` owns pure L3 transforms: atmosphere clamp/target/drift, gated modulation, scar shift clamp, and three-channel scar amplification. It imports only `core.Channel` and stdlib — never `engine`, `tolerance`, `memory`, SQLite, or network. `SensitizationStore` stays in `engine.py` because it is persistent state.

Default `legacy_atmosphere=False` (corrected):

- Atmosphere modulates gated appraisal **before** state write, using the tick-entry atmosphere.
- Shock detection happens after interactions. Current tick shock intensity feeds a 5-entry window, updates atmosphere, and applies from the **next** tick.
- Atmosphere is clamped to `[-1, 1]` at tick entry, after drift, on snapshot load, and on `wake()`.
- Scar amplification only touches sadness / fear / anger, only when `goal_conducibility < 0`; effective shift ∈ [0, 0.6] and max multiplier is 1.6.

`legacy_atmosphere=True` preserves pre-L3 exactly: late gated modulation after state write, zero shock density, no clamp, and the two old `StepTrace.notes`. Snapshot payload includes the flag. `test_l3_atmosphere_scars.py` has 32 tests. There is deliberately no L3 golden: L3 is composition-root ordering, not a pure channel line; L1/L2 four goldens must stay unchanged. Details: `docs/L3_ATMOSPHERE_SCARS.md`.

### Meta-emotion (L3.5 v0, default trace-only)

Meta-emotion is a **second-order process**: an emotion about a primary emotion (e.g., shame about anger), not an 11th channel. `meta_emotion.py` is a pure module importing only stdlib + `core` (enforced by an AST test). It computes `meta_appraisal` (four dimensions), `meta_emotions` (seven kinds), and `regulation_bias` (expression gain, display rule, regulation action, tolerance load bias, reappraisal targets). Default is still trace-only; L5 adds one opt-in consumer for `tolerance_load_bias`.

- Enable only with `EmotionEngine(meta_emotion_enabled=True)` plus `trace=True`; `trace=False` never computes meta-emotion unless L5 load consumption also needs it.
- L5 opt-in: `tolerance_meta_load_enabled=True` (default false) and non-legacy multi tolerance; bias ≤0.2 is added to instant load using the **previous** tick zone / overload. `expression_gain` / `display_rule` remain unconsumed.
- Trace compatibility: disabled → original 27 keys; enabled → adds exactly `meta_enabled`, `meta_appraisal`, `meta_emotions`, `regulation_bias`. No top-level `result["meta_emotion"]`.
- Activation is relative to the channel reference: `felt_diff ≥ 0.04` and `raw_excess ≥ 0.03`; `trust` uses `trust_target(love)`, others use `Personality.baseline()`.
- Neutral default: no profile means no preset “anger→shame”; `MetaEmotionProfile.kind_weights` / `action_bias` are optional character preferences, never written to `Personality`.
- Not persisted: `MetaEmotionState` is per-tick; profile / enable flags are config and stay out of snapshots.
- 25 tests in `test_l3_5_meta_emotion.py`; L5 adds 38, L5.1 adds 12, L4 adds 27, P0 adds 17, L6 adds 53, L7 adds 98, and the full suite is 436. Demo: `characters/meta_emotion_demo.py`. Details: `docs/META_EMOTION.md`.

### Relationship memory (L4 v0.2)

`memory.py` stores relationship-scoped cards/facts/open loops. L4 adds a bounded prior into `Appraisal`; it never pulls `EmotionalState` directly and never writes `Personality`.

- `RelationshipMemoryStore.appraisal_prior()` averages card `appraisal` snapshots against neutral baselines (`expectedness` / `coping_potential` = 0.5), weighted by salience × confidence × exp(−age/30d), and clamps each dimension to ±0.2 (`cap`).
- `core.apply_appraisal_bias()` applies the prior to a **copy** of `Appraisal`, caps each delta, then clamps to the field's legal range. `EmotionEngine.tick(appraisal_bias=...)` calls it before `appraise()`.
- No `Appraisal` → no state effect. Trace keys are unchanged by default; an active prior adds exactly `memory_bias_enabled`, `memory_bias_applied`, `appraisal_before_memory`.
- `respond()` / `respond_llm()` accept an optional `appraisal`; `respond_llm` auto-recalls `appraisal_prior` from `relationship_memory`.
- `MemoryWriteGate` rejects low-confidence candidates, duplicates cards/open loops (Jaccard ≥ 0.8 over 7 days), allows fact-key supersession, and caps 4 writes per turn. `MemoryService.last_gate_decisions` exposes reasons.
- `update_relationship_summary()` regenerates a deterministic ≤160-char summary after successful writes; manual summaries survive until new data arrives.
- `scripts/migrate_legacy_memory.py` migrates `emotion.db` rows idempotently via `raw_ref = legacy:<path>:<id>`; old DB is not deleted.
- 27 tests in `test_l4_memory_integration.py`; 100 turns stay ≤1200 prompt chars; with/without-prior ablation leaves `Personality` unchanged. Details: `docs/MEMORY_SYSTEM.md`.

### Tolerance calibration (L5 v0)

`tolerance.py` defaults to `capacity_model="multi"`: four domain capacities (`anger`, `fear`, `intimacy`, `criticism`) are computed independently, clamped to `[0.35, 1.80]`, then averaged with weights `.25/.30/.25/.20`. Coefficients are calibrated so the weighted means match the old scalar formula (base `.75`, trust `.28`, love `.1375`, neuro `.205`, atm+ `.102`, atm− `.0495`).

- `SCAR_DOMAIN_MAP` compresses only the affected domains: `social_hurt` → anger/fear/criticism; `criticism` → anger/criticism; `cold_shoulder` → fear/intimacy; `self_blame` → intimacy. Same-domain tags use max, not sum.
- `legacy_tolerance=True` forces scalar capacity, global-max scar pressure, and ignores meta bias; `fixtures/l5_tolerance_golden_legacy.json` locks the pre-L5 values.
- `EmotionEngine` fields: `tolerance_config=None`, `legacy_tolerance=False`, `tolerance_meta_load_enabled=False`.
- Snapshots persist `legacy_tolerance` plus multi observation keys (`capacity_model`, `capacity_by_domain`, `meta_load_bias`); `ToleranceConfig` is code-level and must be re-supplied for exact resume.
- L5 golden: 15 scenarios / 41 steps; `scripts/generate_l5_tolerance_golden.py` (`--check`, `--legacy`). Details: `docs/TOLERANCE.md`.

### Expression + dashboard consistency (L5.1)

- `_ZONE_POOLS` gives each character hyper/hypo local lines; `respond()` uses the zone pool instead of the dominant-emotion pool when overloaded. `regulated` still uses `_CHARACTER_POOLS`.
- `_expression_params(state, zone)` preserves the original Live2D mapping for `regulated`; hyper tightens brows/mouth, widens eyes, and suppresses blush/gaze; hypo flattens amplitude, half-closes eyes, and droops the head. Hyper/hypo outputs are clamped to `[-1, 1]`.
- `_action_tendency()` hypo now reduces both approach (`×0.60`) and avoidance (`×0.55`) with a wider `1.35` dominance margin; hyper keeps `×0.85 / ×1.25`. Overloaded `respond()` also sets `needs_llm=True`.
- Dashboard tolerance card: zone background bands, load/capacity lines, four domain capacity lines, current zone/readouts, togglable legend. `/api/state` history points now store the full tolerance dict; `tolerance_history_point()` is the pure helper.
- Tests: `test_l5_1_expression_dashboard.py` (12 tests). Details: `docs/TOLERANCE.md` §7.

### P0 maintainability

`EmotionEngine.tick()` is an orchestrator over ordered `_step_*` methods (`_begin_tick`, decay, slow channels, appraisal, after-appraisal, interactions, shock, memory/saga, meta/tolerance, result). Expression helpers live in `expression.py` (re-exported by `engine.py`); network calls live in `llm_client.py`. Snapshot payloads use `engine.SNAPSHOT_VERSION` + `_migrate_snapshot_payload()`; memory SQLite uses `memory.MEMORY_SCHEMA_VERSION` + `PRAGMA user_version`. Guardrails: `pyproject.toml` (ruff/coverage), `.github/workflows/ci.yml`, `test_p0_maintainability.py`. Details: `docs/ARCHITECTURE.md`.

### L6 JEV / MCP

- `jev.py` is a rule-first text → six-dim `Appraisal` evaluator with bounded LRU cache, batch chunks, optional LLM supplement, and a neutral fallback. It never writes state / personality / memory. `JEVResult` carries `appraisal`, `confidence`, `evidence`, `source`, `cached`.
- LLM is opt-in (`use_llm=True`) and only fires below `confidence_threshold` unless `force_llm=True`; failures keep the rule result. LLM output is validated and clamped via `make_appraisal()`.
- `noul.py` detects text boundary signals (intimacy / command / insult / coercion / threat). Intimacy weight depends on `trust`; the engine still owns the final boundary decision.
- `respond(..., boundary_violation=False, trace=False)` and `respond_llm(..., use_jev=False, jev=None, auto_boundary=False, boundary_violation=None, trace=False)`. All defaults preserve old behavior; results expose `_jev` / `_boundary` and `_jev_error` / `_boundary_error`.
- `mcp_server.py` is a zero-dependency MCP stdio server exposing `tick`, `recall`, `state`; memory is lazy and isolated by `(character_id, user_id)`. `scripts/replay_trace.py` and dashboard `POST /api/replay` provide read-only trace replay.
- 53 tests across `test_l6_jev.py` (22), `test_l6_integration.py` (15), `test_l6_mcp.py` (16). Details: `docs/JEV.md`, `docs/MCP.md`.

### L7 multi-character contagion (v0 + v0.1 bidirectional + v0.2 auto relationship + v0.3 network + v0.4 directional)

`contagion.py` is a pure layer importing only `core.APPRAISAL_LIMITS` + stdlib. v0 does one-way A → B; v0.1 adds bidirectional A ↔ B and a `strength` multiplier; v0.2 derives `strength` from trust / love; v0.3 adds directed multi-hop networks; v0.4 makes A→B and B→A strengths independent. A's input `Appraisal` becomes a bounded prior on B's `Appraisal`; B still uses its own personality, trust/love, tolerance, scars, and atmosphere.

- Transmitted dimensions are exactly `goal_conduciveness`, `expectedness`, `coping_potential`, `social_evaluation`. `goal_relevance` / `other_agency` are deliberately excluded.
- `derive_contagion_bias()` normalizes `expectedness` / `coping_potential` by `(v - 0.5) * 2`, scales by `gain` (default `0.2`, clamped to `[0, 1]`), and clamps each dimension to `±cap` (default `0.08`). Absolute cap is `0.2`; neutral source returns `{}`.
- `ContagionPair(..., strength=1.0)`: `effective_gain = gain * strength`; `strength` is clamped to `[0, 1]`, and `strength=0` disables contagion. It is an explicit per-pair multiplier when `auto_strength=False`.
- `relationship_strength(trust, love)`: weighted `trust × 0.6 + (love / 2) × 0.4`, clamped to `[0, 1]`; invalid inputs count as 0, weights are normalized. `auto_strength=True` / `from_relationship()` averages both engines' strengths and refreshes before every `derive()`. Auto strength only scales `gain`; it never bypasses `cap`.
- `tick_bidirectional()` derives both directions from the two input appraisals before ticking either engine, so results are order-independent and there is no same-tick feedback loop.
- `ContagionNetwork(engines, edges, hop_decay=0.5, max_hops=3)`: directed edges carry `strength`; the first hop uses it directly, later hops multiply by `hop_decay`. The strongest path per `(origin, target)` wins; biases from multiple origins are summed then clamped per dimension to `±cap`. `max_hops` is capped at 8. All node biases are derived before any node ticks.
- `DirectionalRelationship(trust, love, familiarity, boundaries)`: `directional_relationship_strength()` normalizes the three components (defaults 0.6 / 0.4 / 0.2) and applies `1 / (1 + boundary_penalty × count)`; `ContagionPair.from_directional_relationship()` computes independent A→B and B→A strengths and averages them for `pair.strength`. `directional=True` and `auto_strength=True` are mutually exclusive.
- `sanitize_contagion_bias()` is the engine boundary: Mapping only, four known keys, rejects bool / non-numeric / NaN / Inf, clamps, drops zeros, returns `None` when empty.
- `EmotionEngine.tick(..., contagion_bias=None)` applies it after the L4 memory prior and before `appraise()`. Slow channels still run before appraisal, so same-tick love/trust growth sees the original appraisal.
- `result["contagion_bias"]` and three trace keys (`contagion_enabled`, `contagion_applied`, `appraisal_before_contagion`) appear only when contagion actually ran with an appraisal. Default trace remains 27 keys; default result keys are unchanged.
- `ContagionPair(source, target, gain=..., cap=...)`: `tick_pair()` ticks source then target; `tick_target()` ticks only B. Constructor rejects the same engine and shared `state` / `memory` / `scars` / `tolerance`; shared `Personality` is allowed because it is read-only.
- `respond()` / `respond_llm()` forward an optional `contagion_bias`; LLM output still only affects `utterance`.
- No snapshot / memory schema change (`SNAPSHOT_VERSION == 2`, `MEMORY_SCHEMA_VERSION == 1`); contagion is per-tick input, never persisted or stored as memory.
- 98 tests: `test_l7_contagion.py` (14) + `test_l7_integration.py` (13) + `test_l7_1_bidirectional.py` (14) + `test_l7_2_relationship_model.py` (14) + `test_l7_3_network.py` (22) + `test_l7_4_directional.py` (21). Demos: `scripts/multi_character_demo.py [--bidirectional --strength 0.5 --auto-strength]`, `scripts/contagion_network_demo.py`. Details: `docs/L7_MULTI_CHARACTER.md`.
- Deferred: structured relationship history, group-level normalization, learned relationship models.

### Core data flow

```
Appraisal (6-dim cognitive evaluation)
  → optional L4 memory prior (bounded, ±0.2)
  → optional L7 contagion prior (four dims, ±0.08 default, ±0.2 max)
  → raw activation (per-channel)
  → gate_appraisal (trust/love gates modulate input)
  → tolerance negative_gain
  → scar amplification (only sadness/fear/anger; shift ≤ 0.6)
  → atmosphere modulation (tick-entry atmosphere; before state write)
  → state write with saturation
  → boundary trust penalty
  → interaction matrix (activation above per-channel baseline; trust baseline = trust_target(love))
  → shock detection (exp(Δ×2)-1)
  → shock density → atmosphere drift (clamped; applies next tick)
  → optional meta precompute (previous tick zone/overload; default off)
  → tolerance.update (four-domain capacity, typed scar, optional meta bias)
  → memory store (Arousal-driven, 30-min consolidation)
  → respond() or respond_llm() → utterance
```

`legacy_atmosphere=True` instead writes state, then drifts with zero shock, late-modulates gated,
applies boundary/interactions, and detects shock — pre-L3 behavior for exact A/B.

### Key classes

| Class | Purpose |
|---|---|
| `EmotionalState` | 10-channel float state. `felt(ch)` returns log₁₀ perception. `delta(ch)` compares against `_previous` snapshot. |
| `Personality` | OCEAN → `baseline()` maps Big Five to per-channel baselines. Locked — personality doesn't change. |
| `Appraisal` | 6-dim cognitive evaluation: `goal_relevance`, `goal_conduciveness`, `expectedness`, `other_agency`, `coping_potential`, `social_evaluation`. All 0–1. |
| `StepTrace` (`core.py`) | Phase snapshots + L3 atmosphere/scar + L3.5 meta + conditional L4 / L7 bias observations from `tick(trace=True)`; observation only. |
| `EmotionEngine` | Composition root. `tick(appraisal)` runs full update cycle. `wake()` handles offline time. Per-agent instance (one engine = one character). |
| `MetaEmotionProfile` / `MetaEmotionState` (`meta_emotion.py`) | Stable character preferences vs per-tick second-order appraisal. v0 trace-only; not persisted. |
| `MemoryItem` / `MemoryStoreDB` | Legacy SQLite-backed memory. Arousal-driven storage (not valence). 30-min consolidation window (Nielson 2007). |
| `ToleranceState` (`tolerance.py`) | Window of tolerance. Negative-only load, four-domain capacity weighted into `capacity`, typed scar compression, two-tick hysteresis, hyper/hypo control multipliers. `capacity_model="scalar"` reproduces pre-L5. |
| `RelationshipMemoryStore` / `MemoryService` (`memory.py`) | Relationship-scoped long-term memory: cards, facts, relationship summary, open loops. SQLite FTS5 + CJK bigrams. |
| `SensitizationStore` | Kindling (Post 1992) + Bayesian threat (MindLAB 2024). Pattern matching on Appraisal. |
| `ContagionPair` / `ContagionNetwork` / `DirectionalRelationship` (`contagion.py`) | L7 orchestration between isolated engines. Pair: one-way `tick_pair()` / two-way `tick_bidirectional()`; `strength` scales gain; `auto_strength` derives it from trust / love; `directional` uses independent A→B / B→A strengths. Network: directed multi-hop propagation with `hop_decay` / `max_hops`. Never mutates engine personality or state. |

### Channel taxonomy

- **Fast (6):** joy, sadness, anger, fear, disgust, surprise — event-driven with elastic decay (effective half-life shortens with distance from baseline). No hard ceiling; `saturate(x)` provides natural compression.
- **Slow (2):** trust, love — no auto decay. Accumulate via `grow_slow_channels()`. trust follows `trust_target(love)` curve (not fixed baseline). Betrayal (`damage_trust()`) can halve trust in one event.
- **Absence (1):** longing — grows offline (`love × log₁₀(hours)`), decays online.
- **Guilt (1):** requires `self_agency > 0.3` in appraisal — won't activate otherwise.

### Dual clock

`tick()` processes events in real-time. `wake()` handles offline periods by compressing elapsed time per channel:
- anger ×0.08 (gone after sleep), surprise ×0.05
- sadness ×0.30 (penetrates sleep), fear ×0.25
- love ×0.98 (almost untouched), trust ×0.95

### Memory system

Two systems coexist:

- `MemoryStoreDB` (legacy, `engine.py`): SQLite, Arousal-driven storage (Diamond 2007), 30-min consolidation window, tier flash/long_term/short_term. `saga_pull()` runs every 24h.
- `memory.py` (v0.2 / L4): relationship-scoped (`character_id × user_id`) compressed cards, facts, relationship summary, open loops; FTS5 + CJK bigram recall; prompt budget ≤1200 chars. Bounded `appraisal_prior()` enters `engine.tick()` only through six-dimensional `Appraisal`; automatic summary; write gate v2; legacy migration. Memory never writes `Personality`.

### Tolerance window

`tolerance.py` reads the 10 channels after each tick and updates load/capacity/zone. Load only counts negative-channel deviations above baseline plus trust deficit, shock, and boundary violation. Trust/love expand capacity; L5 splits capacity into `anger/fear/intimacy/criticism` and uses a weighted effective capacity. Zones: `regulated`, `hyper`, `hypo`. Two-tick hysteresis; hyper amplifies negative appraisal ×1.5 and doubles negative half-life, hypo reduces negative gain to ×0.55. Low load never triggers hypo. `legacy_tolerance=True` restores scalar capacity + global-max scar; `tolerance_meta_load_enabled=True` optionally adds a bounded (≤0.2) meta load using the previous tick's context.

### LLM fallback chain

`respond_llm()` tries backends in order: primary (DeepSeek) → fallback (Kimi/Moonshot) → local template. Both use OpenAI-compatible `/chat/completions`. The `_llm_backend` key in the result records which backend succeeded.

### Dashboard

`dashboard/server.py` — stdlib HTTP server. `dashboard/index.html` — Chart.js-based SPA.
- `GET /api/state` — full state JSON (10 channels felt+raw, atmosphere, tolerance, dominant, memory stats, scars)
- `GET /api/history?n=120` — recent data points for the line charts; each point includes `tolerance` (`load`, `capacity`, `zone`, `overload_ratio`, `capacity_by_domain`)
- Tolerance card draws zone bands + load/capacity + four-domain capacity lines; the emotion chart remains separate.
- `POST /api/event {"text":"..."}` — feed event text, engine ticks, returns utterance+state
- `POST /api/switch {"character":"kokomi"}` — hot-swap character at runtime
- `POST /api/reset` — reset current character to baseline
- Character loading: `--char` CLI arg → `importlib.import_module(f"characters.{id}")` → calls `create_{id}()` factory
- Generic `classify_generic()` fallback when character's appraise function doesn't handle the text

### Character distillation

`characters/<name>.py` — each defines:
1. `Personality` (OCEAN locked baseline)
2. `<name>_appraise()` — maps event type strings to `Appraisal` objects
3. `create_<name>()` — factory wiring `EmotionEngine(memory=MemoryStoreDB(), scars=SensitizationStore())`

wiki → OCEAN → baseline → appraisal trigger mapping. Two characters on the same engine produce completely different curves — this validates engine differentiation.

### Bridge (furina_bridge.py)

Desktop script, not in repo. Connects engine → VTube Studio (port 8001, expression + face param driving) + TTS (Edge fallback or GPT-SoVITS api_v2 on port 9880). Has `DEEPSEEK_KEY` + `KIMI_KEY` for LLM with automatic fallback, plus an input classifier (`classify_input()`) that maps keywords to Appraisal objects when LLM is off.

## Key design rules

1. **All channels stay positive** — emotions don't cancel each other to negative values. They modulate.
2. **Personality is locked** — OCEAN baseline doesn't change. Characters accumulate "masks" on top.
3. **No hard ceiling** — `saturate(x)` provides natural diminishing returns. `log₁₀` perception compression.
4. **Chinese-first** — comments, variable names in Chinese. README in Chinese. User communicates in Chinese.
5. **Before touching engine.py** — the user has strong opinions on emotion theory. Discuss design changes before implementing. Present paper evidence.
6. **API keys are in bridge scripts**, never in engine.py or committed files.
7. **Golden sets are behavior contracts** — changing `core.py` formulas or L1/L2 reference-line behavior requires regenerating the affected fixture and reviewing every diff. Keep all four L1/L2 legacy/current goldens unchanged unless that path itself changes. New layers must keep them unchanged when disabled. L3 has no golden by design; its contract is pure-function tests, engine A/B, and one exact pre-L3 compatibility value. L3.5 also adds no golden: its contract is trace-key compatibility, bit-identical base state with meta on/off, and 25 dedicated tests. L5 adds two tolerance goldens (`current` multi-domain and frozen `legacy` scalar); `--legacy --check` must always pass, and the legacy fixture is immutable unless the scalar path itself is intentionally changed. L7 has no golden: its contract is default-path key-set compatibility, the 98 dedicated tests, and all six existing goldens remaining unchanged when contagion is off.

## Related files (Desktop, not in repo)

| File | Purpose |
|---|---|
| `furina_bridge.py` | Live2D + TTS + LLM bridge for Furina demo |
| `furina_fresh.py` | Baseline test + scenario sequence for Furina |
| `emotion-engine-下一步.txt` | Original development plan (slightly outdated) |
| `furina_video_script.txt` | B站 promo video script |
