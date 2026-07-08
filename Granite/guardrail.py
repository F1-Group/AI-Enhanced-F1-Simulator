import json

# ─── INPUT GUARDRAIL ───────────────────────────────────────────────────────────

BLOCKED_TOPICS = [
    "weather", "politics", "food", "music", "movie", "sport",
    "football", "basketball", "cricket", "tennis",
    "relationship", "love", "money", "stock", "crypto",
    "joke", "poem", "story", "recipe"
]

RACING_KEYWORDS = [
    "lap", "sector", "tyre", "tire", "brake", "throttle", "gear",
    "speed", "corner", "pit", "overtake", "drs", "fuel", "stint",
    "understeer", "oversteer", "apex", "racing line", "time",
    "wheel", "spin", "rpm", "engineer", "strategy", "gap"
]

def validate_input(question: str):
    question_lower = question.lower()
    for topic in BLOCKED_TOPICS:
        if topic in question_lower:
            return False, f"I can only answer questions related to racing. Please ask about your lap, tyres, strategy, or driving technique."
    has_racing_keyword = any(kw in question_lower for kw in RACING_KEYWORDS)
    if not has_racing_keyword and len(question.split()) > 3:
        return False, "Please ask a question related to your current race or driving performance."
    return True, None


# ─── OUTPUT GUARDRAIL ──────────────────────────────────────────────────────────

MAX_WORDS = 40

INVALID_PHRASES = [
    "i don't know",
    "i cannot",
    "as an ai",
    "i'm not sure",
    "i am not able",
    "i apologize",
    "sorry",
    "i'm unable",
    "please note that",
    "it's important to note"
]

FALLBACK_RESPONSES = {
    "default": "Focus on your braking points and maintain consistent throttle application through the corners.",
    "late_braking": "Move your braking point earlier and trail brake into the apex.",
    "poor_corner_exit": "Apply throttle earlier and more progressively on corner exit.",
    "poor_track_position": "Follow the racing line more closely and avoid large steering corrections.",
    "unstable_throttle": "Use one smooth throttle application instead of pumping the pedal.",
    "sector_time_loss": "Focus on the key corners in the slow sector to recover time.",
}

def validate_output(response: str, error_type: str = "default"):
    response_lower = response.lower()
    for phrase in INVALID_PHRASES:
        if phrase in response_lower:
            return False, FALLBACK_RESPONSES.get(error_type, FALLBACK_RESPONSES["default"])
    word_count = len(response.split())
    if word_count > MAX_WORDS:
        sentences = response.split('.')
        truncated = '. '.join(sentences[:2]).strip()
        if truncated and not truncated.endswith('.'):
            truncated += '.'
        return True, truncated
    if word_count < 3:
        return False, FALLBACK_RESPONSES.get(error_type, FALLBACK_RESPONSES["default"])
    return True, response


# ─── JSON OUTPUT FOR UI TEAM ──────────────────────────────────────────────────

def apply_guardrail(question: str, response: str, error: dict = None):
    """
    Apply guardrails and return a JSON object for the UI team.

    Args:
        question: driver's question
        response: Granite's raw response
        error: optional error dict from error_detection.py

    Returns:
        dict: structured JSON output for UI team
    """
    error_type = error.get("type", "default") if error else "default"
    corner = error.get("corner", None) if error else None
    severity = error.get("severity", None) if error else None

    # Check input
    input_valid, input_error = validate_input(question)
    if not input_valid:
        return {
            "is_valid": False,
            "feedback": input_error,
            "error_type": None,
            "severity": None,
            "corner": None,
            "question": question
        }

    # Check output
    output_valid, cleaned_response = validate_output(response, error_type)

    return {
        "is_valid": output_valid,
        "feedback": cleaned_response,
        "error_type": error_type,
        "severity": severity,
        "corner": corner,
        "question": question
    }


def apply_guardrail_json(question: str, response: str, error: dict = None) -> str:
    """Same as apply_guardrail but returns a JSON string."""
    return json.dumps(apply_guardrail(question, response, error), indent=2)
