from prompts import SYSTEM_PROMPT, build_user_prompt
from granite_client import ask_race_engineer

# Fake data
fake_telemetry = {
    "lap_time": 88.3,
    "best_lap": 86.1,
    "top_speed": 312,
    "throttle": 68,
    "brake": 45,
    "tyre_wear": 78,
    "fuel": 22,
    "sector_1": 28.3,
    "sector_2": 35.1,
    "sector_3": 24.9,
    "laps_remaining": 18,
    "gap_ahead": 2.1,
    "gap_behind": 4.2
}

questions = [
    "Should I pit now?",
    "Why am I losing time in Sector 2?",
    "Can I overtake the car ahead?",
    "Is my fuel level a concern?",
    "What's my biggest weakness this lap?"
]

for question in questions:
    user_prompt = build_user_prompt(fake_telemetry, question)
    answer = ask_race_engineer(SYSTEM_PROMPT, user_prompt)
    print(f"\nQ: {question}")
    print(f"Race engineer: {answer}")