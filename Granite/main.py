from prompts import build_user_prompt
from granite_client import ask_race_engineer
from guardrail import apply_guardrail
from coaching_style import get_system_prompt

fake_telemetry = {
    "lap_time": 88.3,
    "lap_distance": 1820.5,
    "speed_kmh": 212.4,
    "track_pos": 0.15,
    "angle": 0.03,
    "wheel_spin": 0.12,
    "gear": 5,
    "rpm": 11200,
    "race_pos": 3,     
    "fuel": 45.2,        
    "throttle": 0.68,
    "brake": 0.45,
    "steer": -0.12
}

questions = [
    "Should I pit now?",
    "Why am I losing time in Sector 2?",
    "Can I overtake the car ahead?",
    "My wheel spin is high at corner exits, what should I do?",
    "What's my biggest weakness this lap?",
    "What's the weather like today?",
]

style = "aggressive"  # Could change to "supportive" or "technical" 
system_prompt = get_system_prompt(style)

for question in questions:
    user_prompt = build_user_prompt(fake_telemetry, question, track="monza")
    answer = ask_race_engineer(system_prompt, user_prompt)
    is_valid, final_answer = apply_guardrail(question, answer)
    print(f"\nQ: {question}")
    print(f"Race engineer: {final_answer}")