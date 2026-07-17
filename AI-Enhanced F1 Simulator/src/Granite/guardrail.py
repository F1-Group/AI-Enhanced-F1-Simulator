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
    "wheel", "spin", "rpm", "engineer", "strategy", "gap",
    "braking", "detected", "turn", "exit", "entry", "time loss"
]

def validate_input(coaching_request: str):
    request_lower = coaching_request.lower()
    for topic in BLOCKED_TOPICS:
        if topic in request_lower:
            return False, "I can only provide racing coaching feedback. This request is not related to driving performance."
    has_racing_keyword = any(kw in request_lower for kw in RACING_KEYWORDS)
    if not has_racing_keyword and len(coaching_request.split()) > 3:
        return False, "Please provide a racing-related coaching context."
    return True, None


# ─── OUTPUT GUARDRAIL ──────────────────────────────────────────────────────────

MAX_WORDS = 20

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
    "default": "Focus on braking points and consistent throttle application.",
    "late_braking": "Move your braking point earlier and trail brake into the apex.",
    "poor_corner_exit": "Apply throttle earlier and more progressively on corner exit.",
    "poor_track_position": "Follow the racing line and avoid large steering corrections.",
    "unstable_throttle": "Use one smooth throttle application instead of pumping the pedal.",
    "sector_time_loss": "Focus on the key corners in this sector to recover time.",
}

def validate_output(response: str, error_type: str = "default"):
    response_lower = response.lower()
    for phrase in INVALID_PHRASES:
        if phrase in response_lower:
            return False, FALLBACK_RESPONSES.get(error_type, FALLBACK_RESPONSES["default"])

    words = response.split()
    word_count = len(words)

    if word_count > MAX_WORDS:
        # 直接截斷到MAX_WORDS個字
        truncated = ' '.join(words[:MAX_WORDS]).rstrip(',;') + '.'
        return True, truncated

    if word_count < 3:
        return False, FALLBACK_RESPONSES.get(error_type, FALLBACK_RESPONSES["default"])

    return True, response


# ─── JSON OUTPUT FOR UI TEAM ──────────────────────────────────────────────────

def apply_guardrail(coaching_request: str, response: str, error: dict = None):
    """
    Apply guardrails and return a JSON object for the UI team.

    Args:
        coaching_request: coaching context generated from Team 2's error report
        response: Granite's raw response
        error: optional error dict from error_detection.py

    Returns:
        dict: structured JSON output for UI team
    """
    error_type = error.get("type", "default") if error else "default"
    corner = error.get("corner", None) if error else None
    severity = error.get("severity", None) if error else None

    # Check input
    input_valid, input_error = validate_input(coaching_request)
    if not input_valid:
        return {
            "is_valid": False,
            "feedback": input_error,
            "error_type": None,
            "severity": None,
            "corner": None,
            "coaching_context": coaching_request
        }

    # Check output
    output_valid, cleaned_response = validate_output(response, error_type)

    return {
        "is_valid": output_valid,
        "feedback": cleaned_response,
        "error_type": error_type,
        "severity": severity,
        "corner": corner,
        "coaching_context": coaching_request
    }


def apply_guardrail_json(coaching_request: str, response: str, error: dict = None) -> str:
    """Same as apply_guardrail but returns a JSON string."""
    return json.dumps(apply_guardrail(coaching_request, response, error), indent=2)


def apply_guardrail_simple(question: str, response: str):
    """Returns (is_valid, text) tuple for granite_adapter.py compatibility."""
    result = apply_guardrail(question, response)
    return result["is_valid"], result["feedback"]