import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prompts import build_user_prompt
from granite_client import ask_race_engineer
from guardrail import apply_guardrail
from coaching_style import get_system_prompt
from rag import retrieve, load_knowledge_base

# Load knowledge base on startup
load_knowledge_base()

# Fake telemetry data (aligned with team schema)
fake_telemetry = {
    "timestamp": 45.3,
    "lap_distance": 1820.5,
    "speed_kmh": 212.4,
    "track_pos": 0.15,
    "angle": 0.03,
    "wheel_spin": 0.12,
    "lap_time": 88.3,
    "best_lap": 86.1,
    "throttle": 0.68,
    "brake": 0.45,
    "steer": -0.12,
    "gear": 5,
    "rpm": 11200,
    "sector_1": 28.3,
    "sector_2": 35.1,
    "sector_3": 24.9,
    "laps_remaining": 18,
    "gap_ahead": 2.1,
    "gap_behind": 4.2
}

# Mock errors from Analysis team (error_detection.py format)
mock_errors = [
    {
        "tag": "T1_late_braking",
        "corner": "T1",
        "type": "late_braking",
        "severity": "high",
        "confidence": 0.85,
        "coaching_hint": "Brake about 25m earlier before T1 to stabilise corner entry.",
        "evidence": {
            "expert_brake_point_m": 275.0,
            "player_brake_point_m": 300.0,
            "braked_late_by_m": 25.0,
            "entry_overspeed_kmh": 18.3
        }
    },
    {
        "tag": "T5_poor_corner_exit",
        "corner": "T5",
        "type": "poor_corner_exit",
        "severity": "medium",
        "confidence": 0.75,
        "coaching_hint": "Get on the throttle earlier out of T5; you are exiting 14 km/h slower than the baseline.",
        "evidence": {
            "exit_speed_deficit_kmh": 14.0,
            "mean_throttle_gap": -0.18
        }
    },
    {
        "tag": "S2_time_loss",
        "corner": "Sector 2",
        "type": "sector_time_loss",
        "severity": "medium",
        "confidence": 0.70,
        "coaching_hint": "You lost about 2.1s in Sector 2; focus on Lesmos and Ascari.",
        "evidence": {
            "time_loss_s": 2.1,
            "sector_start_m": 1500.0,
            "sector_end_m": 3500.0
        }
    }
]

questions = [
    "Should I pit now?",
    "Why am I losing time in Sector 2?",
    "Can I overtake the car ahead?",
    "My wheel spin is high at corner exits, what should I do?",
    "What's my biggest weakness this lap?",
    "What's the weather like today?",
]

# Coaching style: aggressive / supportive / technical
style = "technical"
system_prompt = get_system_prompt(style)

for question in questions:
    # Retrieve relevant knowledge from RAG
    knowledge_chunks = retrieve(question, top_k=3)
    knowledge_context = "\n\n".join(knowledge_chunks)

    # Build prompt with RAG knowledge + errors from Analysis team
    user_prompt = build_user_prompt(
        fake_telemetry,
        question,
        track="olethros_road_1",
        knowledge=knowledge_context,
        errors=mock_errors
    )

    answer = ask_race_engineer(system_prompt, user_prompt)
    result = apply_guardrail(question, answer, error=mock_errors[0] if mock_errors else None)

    print(f"\nQ: {question}")
    print(f"Race engineer: {result['feedback']}")
    print(f"Output JSON: {result}")