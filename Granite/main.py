from prompts import SYSTEM_PROMPT, build_user_prompt
from granite_client import ask_race_engineer
from guardrail import apply_guardrail

# Fake telemetry data (aligned with team schema)
fake_telemetry = {
    "timestamp": 45.3,          # seconds into current lap
    "lap_distance": 1820.5,     # meters from start
    "speed_kmh": 212.4,         # forward speed in km/h
    "track_pos": 0.15,          # lateral offset from centerline (-1 to 1)
    "angle": 0.03,              # angle between car and track direction
    "wheel_spin": 0.12,         # wheel slip indicator
    "lap_time": 88.3,           # current lap accumulated time (s)
    "best_lap": 86.1,           # best lap reference (s)
    "throttle": 0.68,           # throttle input (0.0 ~ 1.0)
    "brake": 0.45,              # brake input (0.0 ~ 1.0)
    "steer": -0.12,             # steering angle (-1.0 ~ 1.0)
    "gear": 5,                  # current gear (-1 ~ 6)
    "rpm": 11200,               # engine RPM
    "sector_1": 28.3,           # sector 1 time (s)
    "sector_2": 35.1,           # sector 2 time (s)
    "sector_3": 24.9,           # sector 3 time (s)
    "laps_remaining": 18,       # laps remaining in session
    "gap_ahead": 2.1,           # gap to car ahead (s)
    "gap_behind": 4.2           # gap to car behind (s)
}

questions = [
    "Should I pit now?",
    "Why am I losing time in Sector 2?",
    "Can I overtake the car ahead?",
    "My wheel spin is high at corner exits, what should I do?",
    "What's my biggest weakness this lap?",
    "What's the weather like today?",
]

for question in questions:
    user_prompt = build_user_prompt(fake_telemetry, question, track="monza")
    answer = ask_race_engineer(SYSTEM_PROMPT, user_prompt)
    is_valid, final_answer = apply_guardrail(question, answer)
    print(f"\nQ: {question}")
    print(f"Race engineer: {final_answer}")