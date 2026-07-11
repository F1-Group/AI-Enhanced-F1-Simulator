from .rag import retrieve

# Track dictionary - F1 track knowledge base
TRACK_KNOWLEDGE = {
    "monza": {
        "name": "Autodromo Nazionale Monza",
        "key_corners": "Variante del Rettifilo (T1), Curva Grande, Variante della Roggia, Lesmos (T5-T6), Variante Ascari, Parabolica (T11)",
        "characteristics": "Low downforce, high speed circuit. Long straights favour top speed. Heavy braking zones at T1 and Variante Ascari.",
        "sector_notes": "S1: chicane braking, S2: Lesmos and Ascari, S3: Parabolica exit onto main straight",
        "tyre_info": "Low tyre wear circuit. Softs can run long. Fuel saving possible on straights."
    },
    "silverstone": {
        "name": "Silverstone Circuit",
        "key_corners": "Abbey, Farm, Village, The Loop, Arpex, Wellington, Brooklands, Luffield, Woodcote, Copse, Maggotts, Becketts, Chapel, Stowe, Vale, Club",
        "characteristics": "High speed, high downforce circuit. Maggotts-Becketts complex is key. Wind plays major role.",
        "sector_notes": "S1: Copse and Maggotts-Becketts, S2: Stowe and Vale, S3: Club to finish",
        "tyre_info": "High lateral load causes tyre wear on rear. Manage rear degradation through Becketts."
    },
    "spa": {
        "name": "Circuit de Spa-Francorchamps",
        "key_corners": "La Source (T1), Eau Rouge, Raidillon, Kemmel Straight, Les Combes, Pouhon, Blanchimont, Bus Stop",
        "characteristics": "Mixed circuit with high speed and technical sections. Eau Rouge-Raidillon is iconic. Weather can change rapidly.",
        "sector_notes": "S1: La Source and Eau Rouge, S2: Kemmel and Les Combes, S3: Pouhon to Bus Stop",
        "tyre_info": "High energy circuit. Blanchimont flat requires confidence. Tyre warm-up critical in cold conditions."
    },
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
        track: track name (monza, silverstone, spa, olethros_road_1, generic)
        knowledge: RAG knowledge string from rag.retrieve()
        errors: list of error dicts from error_detection.detect_errors()
    """
    # Calculate lap delta
    lap_delta = round(telemetry['lap_time'] - telemetry['best_lap'], 2)

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
- Timestamp: {telemetry['timestamp']}s
- Lap distance: {telemetry['lap_distance']}m
- Speed: {telemetry['speed_kmh']} km/h
- Track position (centerline offset): {telemetry['track_pos']}
- Car angle vs track: {telemetry['angle']}
- Wheel spin: {telemetry['wheel_spin']}
- Current lap time: {telemetry['lap_time']}s (delta to best: +{lap_delta}s)
- Best lap reference: {telemetry['best_lap']}s
- Throttle input: {telemetry['throttle']}
- Brake input: {telemetry['brake']}
- Steering angle: {telemetry['steer']}
- Gear: {telemetry['gear']}
- RPM: {telemetry['rpm']}
- Sector 1: {telemetry['sector_1']}s
- Sector 2: {telemetry['sector_2']}s
- Sector 3: {telemetry['sector_3']}s
- Laps remaining: {telemetry['laps_remaining']}
- Gap to car ahead: {telemetry['gap_ahead']}s
- Gap to car behind: {telemetry['gap_behind']}s

{errors_section}

{knowledge_section}
COACHING CONTEXT: {coaching_request}

REPLY IN ONE SENTENCE ONLY. Maximum 20 words. Be direct and specific. No explanations.

REPLY IN ONE SENTENCE ONLY. Maximum 20 words. Be direct and specific. No explanations.
"""