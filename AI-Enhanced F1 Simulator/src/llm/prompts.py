# Static track knowledge shown to the LLM (no per-track data yet, so this is
# generic sector/tyre guidance rather than anything circuit-specific).
TRACK_NAME = "Current Circuit"
TRACK_KEY_CORNERS = "analyse based on sector times"
TRACK_CHARACTERISTICS = "Focus on sector time differences to identify problem areas"
TRACK_SECTOR_NOTES = "Sector1, Sector2, Sector3 performance compared to best lap reference"
TRACK_TYRE_INFO = "Monitor tyre wear and adjust driving style accordingly"


def build_user_prompt(telemetry, coaching_request, knowledge=""):
    """
    Build the user prompt for Granite.

    Args:
        telemetry: dict of telemetry data (aligned with team schema)
        coaching_request: coaching context generated from Team 2's error report
        knowledge: RAG knowledge string from rag.retrieve()
    """

    # Format optional section
    knowledge_section = (
        f"GENERAL BACKGROUND CONCEPT (not evidence about this specific lap — "
        f"any corner, chicane, or number mentioned here is just an illustrative "
        f"example from the knowledge base, not something that happened in this "
        f"drive):\n{knowledge}\n"
    ) if knowledge else ""

    return f"""
CURRENT TRACK: {TRACK_NAME}
Key corners: {TRACK_KEY_CORNERS}
Track characteristics: {TRACK_CHARACTERISTICS}
Sector notes: {TRACK_SECTOR_NOTES}
Tyre advice: {TRACK_TYRE_INFO}

TELEMETRY DATA (a single instantaneous snapshot, no expert comparison — do not
invent a target/adjustment number for any of these fields unless that same
field also appears in the Evidence below):
- Lap distance: {telemetry['lap_distance']}m
- Speed: {telemetry['speed_kmh']} km/h
- Track position (centerline offset): {telemetry['track_pos']}
- Car angle vs track: {telemetry['angle']}
- Wheel spin: {telemetry['wheel_spin']}
- Current lap time: {telemetry['lap_time']}s
- Throttle input: {telemetry['throttle']}
- Brake input: {telemetry['brake']}
- Steering angle: {telemetry['steer']}
- Gear: {telemetry['gear']}
- RPM: {telemetry['rpm']}

{knowledge_section}
COACHING CONTEXT: {coaching_request}

REPLY IN ONE SENTENCE ONLY. Maximum 20 words.
"""
