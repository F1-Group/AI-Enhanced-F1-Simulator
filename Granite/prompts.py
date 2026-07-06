from rag import retrieve

def build_user_prompt(telemetry, question):
    # Use RAG to search relevent knowledge
    relevant_chunks = retrieve(question, top_k=3)
    rag_knowledge = "\n".join(relevant_chunks)

    print("=== RAG RETRIEVED ===")
    print(rag_knowledge)
    print("======================")

    return f"""
RELEVANT KNOWLEDGE:
{rag_knowledge}

TELEMETRY DATA:
- Lap time: {telemetry['lap_time']}s
- Lap distance: {telemetry['lap_distance']}m
- Speed: {telemetry['speed_kmh']} km/h
- Track position (centerline offset): {telemetry['track_pos']}
- Car angle vs track direction: {telemetry['angle']} rad
- Wheel spin: {telemetry['wheel_spin']} rad/s
- Gear: {telemetry['gear']}
- RPM: {telemetry['rpm']}
- Race position: {telemetry['race_pos']}
- Fuel remaining: {telemetry['fuel']}L
- Throttle input: {telemetry['throttle']}
- Brake input: {telemetry['brake']}
- Steering angle: {telemetry['steer']}

DRIVER QUESTION: {question}
"""