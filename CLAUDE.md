# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

多通道情绪交互引擎 — a 10-channel emotion engine for AI companions. Replaces the 1974 PAD (Pleasure-Arousal-Dominance) model with independent emotion channels that modulate each other without canceling. MIT license. ~1400 lines in a single-file core.

Owner: 枫骅 (fenghua2006). GitHub: `github.com/fenghua2006/emotion-engine`.

## Running

```bash
cd C:\Users\Administrator\Desktop\emotion-engine

# Core engine demo
uv run python engine.py

# Character tests (3 distilled characters)
uv run python characters/furina.py
uv run python characters/kokomi.py
uv run python characters/columbina.py

# Scenario test
uv run python test_iloveyou.py

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

Single file: `engine.py` (~1400 lines). No package structure. `characters/` holds per-character distillations. `dashboard/` holds HTTP server + HTML panel.

### Core data flow

```
Appraisal (6-dim cognitive evaluation)
  → raw activation (per-channel)
  → gate_appraisal (trust/love gates modulate input)
  → scar amplification (SensitizationStore)
  → tick() — apply to state with saturation
  → atmosphere drift (shock density → tense/relaxed)
  → atmosphere modulation (amplifies or buffers channels)
  → interaction matrix (cross-channel modulation)
  → shock detection (exp(Δ×2)-1)
  → memory store (Arousal-driven, 30-min consolidation)
  → respond() or respond_llm() → utterance
```

### Key classes (all in `engine.py`)

| Class | Purpose |
|---|---|
| `EmotionalState` | 10-channel float state. `felt(ch)` returns log₁₀ perception. `delta(ch)` compares against `_previous` snapshot. |
| `Personality` | OCEAN → `baseline()` maps Big Five to per-channel baselines. Locked — personality doesn't change. |
| `Appraisal` | 6-dim cognitive evaluation: `goal_relevance`, `goal_conduciveness`, `expectedness`, `other_agency`, `coping_potential`, `social_evaluation`. All 0–1. |
| `EmotionEngine` | Core engine. `tick(appraisal)` runs full update cycle. `wake()` handles offline time. Per-agent instance (one engine = one character). |
| `MemoryItem` / `MemoryStoreDB` | SQLite-backed memory. Arousal-driven storage (not valence). 30-min consolidation window (Nielson 2007). |
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

`MemoryStoreDB` (SQLite). Storage is Arousal-driven (Diamond 2007), not valence-driven. 30-min consolidation window determines tier: flash (>0.6 score) → long_term (>0.3) → short_term (discarded after 7 days). Recall by arousal match + tier weight + recency. Sadness >0.25 enables depressive realism (fewer positive biases in recall). `saga_pull()` runs every 24h — long-term memories exert gentle pull toward remembered states.

### LLM fallback chain

`respond_llm()` tries backends in order: primary (DeepSeek) → fallback (Kimi/Moonshot) → local template. Both use OpenAI-compatible `/chat/completions`. The `_llm_backend` key in the result records which backend succeeded.

### Dashboard

`dashboard/server.py` — stdlib HTTP server. `dashboard/index.html` — Chart.js-based SPA.
- `GET /api/state` — full state JSON (10 channels felt+raw, atmosphere, dominant, memory stats, scars)
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

## Related files (Desktop, not in repo)

| File | Purpose |
|---|---|
| `furina_bridge.py` | Live2D + TTS + LLM bridge for Furina demo |
| `furina_fresh.py` | Baseline test + scenario sequence for Furina |
| `emotion-engine-下一步.txt` | Original development plan (slightly outdated) |
| `furina_video_script.txt` | B站 promo video script |
