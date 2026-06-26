# guardrail.py
# Filters input questions and output responses for the F1 coaching system

# ─── INPUT GUARDRAIL ───────────────────────────────────────────────────────────

# Keywords that are NOT related to F1 / racing
BLOCKED_TOPICS = [
    "weather", "politics", "food", "music", "movie", "sport",
    "football", "basketball", "cricket", "tennis",
    "relationship", "love", "money", "stock", "crypto",
    "joke", "poem", "story", "recipe"
]

# Keywords that confirm the question IS related to F1 / racing
RACING_KEYWORDS = [
    "lap", "sector", "tyre", "tire", "brake", "throttle", "gear",
    "speed", "corner", "pit", "overtake", "drs", "fuel", "stint",
    "understeer", "oversteer", "apex", "racing line", "time",
    "wheel", "spin", "rpm", "engineer", "strategy", "gap"
]

def validate_input(question: str):
    """
    Check if the question is related to F1 / racing.
    Returns (is_valid: bool, error_message: str or None)
    """
    question_lower = question.lower()

    # Block if contains non-racing topics
    for topic in BLOCKED_TOPICS:
        if topic in question_lower:
            return False, f"I can only answer questions related to racing. Please ask about your lap, tyres, strategy, or driving technique."

    # Warn if no racing keywords found
    has_racing_keyword = any(kw in question_lower for kw in RACING_KEYWORDS)
    if not has_racing_keyword and len(question.split()) > 3:
        return False, "Please ask a question related to your current race or driving performance."

    return True, None


# ─── OUTPUT GUARDRAIL ──────────────────────────────────────────────────────────

MAX_WORDS = 40

# Phrases that indicate Granite gave a bad response
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

# Fallback responses if Granite gives a bad response
FALLBACK_RESPONSES = {
    "default": "Focus on your braking points and maintain consistent throttle application through the corners.",
    "wheel_spin": "Reduce throttle past the apex to minimise wheelspin and preserve rear tyre grip.",
    "heavy_braking": "Move your braking point 10 meters earlier and trail brake into the apex.",
    "track_limit": "Use a later apex to open the corner exit and avoid running wide.",
    "misaligned": "Reduce steering input and wait for the car to settle before applying throttle.",
}

def validate_output(response: str, event_type: str = "default"):
    """
    Check if Granite's response is valid.
    Returns (is_valid: bool, cleaned_response: str)
    """
    # Check for invalid phrases
    response_lower = response.lower()
    for phrase in INVALID_PHRASES:
        if phrase in response_lower:
            return False, FALLBACK_RESPONSES.get(event_type, FALLBACK_RESPONSES["default"])

    # Check if response is too long
    word_count = len(response.split())
    if word_count > MAX_WORDS:
        # Truncate to first two sentences
        sentences = response.split('.')
        truncated = '. '.join(sentences[:2]).strip()
        if truncated and not truncated.endswith('.'):
            truncated += '.'
        return True, truncated

    # Check if response is too short (probably useless)
    if word_count < 3:
        return False, FALLBACK_RESPONSES.get(event_type, FALLBACK_RESPONSES["default"])

    return True, response


# ─── COMBINED GUARDRAIL ────────────────────────────────────────────────────────

def apply_guardrail(question: str, response: str, event_type: str = "default"):
    """
    Apply both input and output guardrails.
    Returns (is_valid: bool, final_response: str)
    """
    # Check input
    input_valid, input_error = validate_input(question)
    if not input_valid:
        return False, input_error

    # Check output
    output_valid, cleaned_response = validate_output(response, event_type)
    return output_valid, cleaned_response
