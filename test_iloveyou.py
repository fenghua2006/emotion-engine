"""Test: dual-mode respond() — local (0 token) and LLM-enhanced."""
import sys, os
sys.path.insert(0, '.')

from engine import Appraisal, respond, _build_llm_prompt
from characters.kokomi import create_kokomi

engine = create_kokomi()

love_event = Appraisal(
    goal_relevance=1.0, goal_conduciveness=0.9,
    expectedness=0.1, other_agency=1.0,
    coping_potential=0.2, social_evaluation=1.0
)

# Mode 1: Local only — always works, zero cost
r_local = respond(engine, "love_confession", love_event, character_id="kokomi")
print("=== Mode 1: Local (0 tokens, no API key) ===")
print(f"  say: \"{r_local['utterance']}\"")
print(f"  action: {r_local['action']}")
print(f"  needs_llm: {r_local['needs_llm']}")
print(f"  dominant: {r_local['_dominant']}")
print()

# Mode 2: LLM-enhanced — richer expression (optional API key)
print("=== Mode 2: LLM prompt (what would be sent) ===")
prompt = _build_llm_prompt(
    engine, "I love you",
    "Kokomi", "A brilliant strategist who leads an island nation. "
              "Socially anxious, overthinks everything, speaks formally. "
              "Beneath the commander's composure is a fragile heart.",
    []
)
print(f"  Token estimate: ~{len(prompt)//4} tokens")
print(f"  Prompt preview:")
for line in prompt.split("\n")[:8]:
    print(f"    {line}")
print(f"    ...")

# Mode 1 repeated — trust builds, LLM no longer needed
for _ in range(3):
    engine.state._last_update -= 3600
    r = respond(engine, "love_confirmation", love_event, character_id="kokomi")
print(f"\n  After 3 more 'I love you's: needs_llm={r['needs_llm']}")
print(f"  say: \"{r['utterance']}\"")
print()
print("Setup: respond(use_llm=True) activates Mode 2 automatically when API key present.")
print("       Without API key → Mode 1 only. Engine never breaks.")
