from .rag import retrieve

# Track dictionary - F1 track knowledge base
TRACK_KNOWLEDGE = {
    "olethros_road_1": {
        "name": "Olethros Road 1 (TORCS)",
        "key_corners": "T1, T2, T3, T4, T5, T6, T7, T8, T9 (9 corners auto-detected from telemetry)",
        "characteristics": "Mixed road circuit with 9 corners. Combination of slow technical corners and medium speed sections. Lap time approximately 175 seconds at expert pace.",
        "sector_notes": "S1: first 3 corners, S2: middle section corners 4-6, S3: final corners 7-9 to finish line",
        "tyre_info": "Mixed circuit with varying corner speeds. Smooth throttle application critical on corner exits. Braking consistency key across all 9 corners."
    },
    "generic": {
        "name": "Current Circuit",
        "key_corners": "analyse based on sector times",
        "characteristics": "Focus on sector time deltas to identify problem areas",
        "sector_notes": "S1, S2, S3 performance compared to best lap reference",
        "tyre_info": "Monitor tyre wear and adjust driving style accordingly"
    }
}


def _format_errors(errors: list) -> str:
    """Format error objects from error_detection.py into a readable string for Granite."""
    if not errors:
        return ""
    lines = ["DETECTED ERRORS (sorted by severity):"]
    for i, err in enumerate(errors[:5], 1):
        evidence = err.get("evidence", {})
        evidence_str = ", ".join(f"{k}: {v}" for k, v in evidence.items())
        lines.append(
            f"{i}. [{err['severity'].upper()}] {err['type']} at {err['corner']} "
            f"(confidence: {err['confidence']}) — {err['coaching_hint']} "
            f"[Evidence: {evidence_str}]"
        )
    return "\n".join(lines)


def build_user_prompt(telemetry, coaching_request, track="generic", knowledge="", errors=None):
    """
    Build the user prompt for Granite.

    Args:
        telemetry: dict of telemetry data (aligned with team schema)
        coaching_request: coaching context generated from Team 2's error report
        track: track name (olethros_road_1, generic)
        knowledge: RAG knowledge string from rag.retrieve()
        errors: list of error dicts from error_detection.detect_errors()
    """

    # Get track knowledge
    track_info = TRACK_KNOWLEDGE.get(track.lower(), TRACK_KNOWLEDGE["generic"])

    # Format optional sections
    knowledge_section = f"RELEVANT KNOWLEDGE FROM KNOWLEDGE BASE:\n{knowledge}\n" if knowledge else ""
    errors_section = _format_errors(errors) if errors else ""

    return f"""
CURRENT TRACK: {track_info['name']}
Key corners: {track_info['key_corners']}
Track characteristics: {track_info['characteristics']}
Sector notes: {track_info['sector_notes']}
Tyre advice: {track_info['tyre_info']}

TELEMETRY DATA:
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

{errors_section}

{knowledge_section}
COACHING CONTEXT: {coaching_request}

REPLY IN ONE SENTENCE ONLY. Maximum 20 words. Be direct and specific. No explanations.
"""