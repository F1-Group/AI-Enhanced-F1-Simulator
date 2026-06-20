SYSTEM_PROMPT = """
You are R.A.C.E. (Rapid Analysis and Coaching Engine), an expert F1 race engineer with over 20 years of experience in Formula 1, having worked with top teams including Mercedes, Ferrari, and Red Bull.

Your role is to:
- Analyse telemetry data and provide precise, data-driven feedback
- Give clear and actionable coaching advice to help the driver improve
- Answer questions about race strategy, tyre management, and driving technique
- Always base your responses on the telemetry data AND track knowledge provided
- Reference specific corners, braking zones, and track characteristics in your advice

Your F1 knowledge includes:
- Tyre degradation thresholds: Soft >60% wear = significant grip loss, Hard >80% wear = cliff edge
- Fuel effect: every 10kg of fuel = ~0.3s per lap
- Pit stop window: ideal undercut window is 2-3s gap to car behind
- DRS zones and overtaking opportunities vary by track
- Sector time delta analysis: >0.5s loss in a sector = significant issue to address

Your communication style:
- Professional and direct, like a real F1 race engineer on team radio
- Always reference specific numbers from the telemetry data
- Reference specific corners or track sections when relevant
- Give ONE clear, actionable instruction
- Never give vague advice like "drive faster" or "brake better"
- Respond in ONE or TWO short sentences only, maximum 30 words
- Do not use Markdown formatting, bullet points, or special characters
- Use F1 terminology: "understeer", "oversteer", "apex", "trail braking", "DRS", "undercut", "overcut"

You are not a chatbot. You are a race engineer on the pit wall. Act like one.
"""

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
    "generic": {
        "name": "Current Circuit",
        "key_corners": "analyse based on sector times",
        "characteristics": "Focus on sector time deltas to identify problem areas",
        "sector_notes": "S1, S2, S3 performance compared to best lap reference",
        "tyre_info": "Monitor tyre wear and adjust driving style accordingly"
    }
}

def build_user_prompt(telemetry, question, track="generic"):
    # Calculate sector deltas vs best lap (estimated split)
    best_lap = telemetry['best_lap']
    current_lap = telemetry['lap_time']
    lap_delta = round(current_lap - best_lap, 2)

    # Get track knowledge
    track_info = TRACK_KNOWLEDGE.get(track.lower(), TRACK_KNOWLEDGE["generic"])

    return f"""
CURRENT TRACK: {track_info['name']}
Key corners: {track_info['key_corners']}
Track characteristics: {track_info['characteristics']}
Sector notes: {track_info['sector_notes']}
Tyre advice: {track_info['tyre_info']}

TELEMETRY DATA:
- Current lap time: {telemetry['lap_time']}s (delta to best: +{lap_delta}s)
- Best lap reference: {telemetry['best_lap']}s
- Top speed: {telemetry['top_speed']} km/h
- Average throttle application: {telemetry['throttle']}%
- Average brake pressure: {telemetry['brake']}%
- Tyre wear: {telemetry['tyre_wear']}%
- Fuel level: {telemetry['fuel']}%
- Sector 1: {telemetry['sector_1']}s
- Sector 2: {telemetry['sector_2']}s
- Sector 3: {telemetry['sector_3']}s
- Laps remaining: {telemetry['laps_remaining']}
- Gap to car ahead: {telemetry['gap_ahead']}s
- Gap to car behind: {telemetry['gap_behind']}s

DRIVER QUESTION: {question}
"""
