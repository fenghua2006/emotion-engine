# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

多通道情绪交互引擎 — a 10-channel emotion engine for AI companions. Replaces the 1974 PAD (Pleasure-Arousal-Dominance) model with independent emotion channels that modulate each other without canceling. MIT license. Pure dynamics live in `core.py`; `engine.py` is the composition root (~1,650 lines) plus `atmosphere.py`, `meta_emotion.py`, `tolerance.py`, and `memory.py` modules.

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
python -m unittest test_memory_system.py -v
python -m unittest test_l1_fast_channels.py test_interaction_activation.py -v
python -m unittest test_l2_slow_channels.py -v
python -m unittest test_l3_atmosphere_scars.py -v
python -m unittest test_l3_5_meta_emotion.py -v

# Full suite (191 tests)
python -m unittest discover

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

Core dynamics: `core.py` (no memory/tolerance/LLM imports). Atmosphere/scar pure transforms: `atmosphere.py`. Meta-emotion pure layer: `meta_emotion.py`. Composition root: `engine.py` (~1,650 lines). Control layer: `tolerance.py`. Relationship memory: `memory.py`. No package structure yet. `characters/` holds per-character distillations. `dashboard/` holds HTTP server + HTML panel.

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

### Meta-emotion (L3.5 v0, trace-only)

Meta-emotion is a **second-order process**: an emotion about a primary emotion (e.g., shame about anger), not an 11th channel. `meta_emotion.py` is a pure module importing only stdlib + `core` (enforced by an AST test). It computes `meta_appraisal` (four dimensions), `meta_emotions` (seven kinds), and `regulation_bias` (expression gain, display rule, regulation action, tolerance load bias, reappraisal targets), but v0 **does not consume** any bias: `respond()`, tolerance, and the next tick stay untouched.

- Enable only with `EmotionEngine(meta_emotion_enabled=True)` plus `trace=True`; `trace=False` never computes meta-emotion.
- Trace compatibility: disabled → original 27 keys; enabled → adds exactly `meta_enabled`, `meta_appraisal`, `meta_emotions`, `regulation_bias`. No top-level `result["meta_emotion"]`.
- Activation is relative to the channel reference: `felt_diff ≥ 0.04` and `raw_excess ≥ 0.03`; `trust` uses `trust_target(love)`, others use `Personality.baseline()`.
- Neutral default: no profile means no preset “anger→shame”; `MetaEmotionProfile.kind_weights` / `action_bias` are optional character preferences, never written to `Personality`.
- Not persisted: `MetaEmotionState` is per-tick; profile / enable flag are config and stay out of snapshots.
- 25 tests in `test_l3_5_meta_emotion.py`; full suite 191 tests. Demo: `characters/meta_emotion_demo.py`. Details: `docs/META_EMOTION.md`.

### Core data flow

```
Appraisal (6-dim cognitive evaluation)
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
  → tolerance.update (load / capacity / zone, affects next tick)
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
| `StepTrace` (`core.py`) | Phase snapshots + L3 atmosphere/scar + L3.5 meta observations from `tick(trace=True)`; observation only. |
| `EmotionEngine` | Composition root. `tick(appraisal)` runs full update cycle. `wake()` handles offline time. Per-agent instance (one engine = one character). |
| `MetaEmotionProfile` / `MetaEmotionState` (`meta_emotion.py`) | Stable character preferences vs per-tick second-order appraisal. v0 trace-only; not persisted. |
| `MemoryItem` / `MemoryStoreDB` | Legacy SQLite-backed memory. Arousal-driven storage (not valence). 30-min consolidation window (Nielson 2007). |
| `ToleranceState` (`tolerance.py`) | Window of tolerance. Negative-only load, trust/love expand capacity, two-tick hysteresis, hyper/hypo control multipliers. |
| `RelationshipMemoryStore` / `MemoryService` (`memory.py`) | Relationship-scoped long-term memory: cards, facts, relationship summary, open loops. SQLite FTS5 + CJK bigrams. |
| `SensitizationStore` | Kindling (Post 1992) + Bayesian threat (MindLAB 2024). Pattern matching on Appraisal. |

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
- `memory.py` (v0.1): relationship-scoped (`character_id × user_id`) compressed cards, facts, relationship summary, open loops; FTS5 + CJK bigram recall; prompt budget ≤1200 chars. Memory never writes `Personality`.

### Tolerance window

`tolerance.py` reads the 10 channels after each tick and updates load/capacity/zone. Load only counts negative-channel deviations above baseline plus trust deficit, shock, and boundary violation. Trust/love expand capacity. Zones: `regulated`, `hyper`, `hypo`. Two-tick hysteresis; hyper amplifies negative appraisal ×1.5 and doubles negative half-life, hypo reduces negative gain to ×0.55. Low load never triggers hypo.

### LLM fallback chain

`respond_llm()` tries backends in order: primary (DeepSeek) → fallback (Kimi/Moonshot) → local template. Both use OpenAI-compatible `/chat/completions`. The `_llm_backend` key in the result records which backend succeeded.

### Dashboard

`dashboard/server.py` — stdlib HTTP server. `dashboard/index.html` — Chart.js-based SPA.
- `GET /api/state` — full state JSON (10 channels felt+raw, atmosphere, tolerance, dominant, memory stats, scars)
- `GET /api/history?n=120` — recent data points for the line chart
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
7. **Golden sets are behavior contracts** — changing `core.py` formulas or L1/L2 reference-line behavior requires regenerating the affected fixture and reviewing every diff. Keep all four L1/L2 legacy/current goldens unchanged unless that path itself changes. New layers must keep them unchanged when disabled. L3 has no golden by design; its contract is pure-function tests, engine A/B, and one exact pre-L3 compatibility value. L3.5 also adds no golden: its contract is trace-key compatibility, bit-identical base state with meta on/off, and 25 dedicated tests.

## Related files (Desktop, not in repo)

| File | Purpose |
|---|---|
| `furina_bridge.py` | Live2D + TTS + LLM bridge for Furina demo |
| `furina_fresh.py` | Baseline test + scenario sequence for Furina |
| `emotion-engine-下一步.txt` | Original development plan (slightly outdated) |
| `furina_video_script.txt` | B站 promo video script |
