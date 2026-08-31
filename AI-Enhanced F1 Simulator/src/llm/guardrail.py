import json
import re

# Input guardrail

BLOCKED_TOPICS = [
    "politics", "food", "music", "movie", "sport",
    "football", "basketball", "cricket", "tennis",
    "relationship", "love", "crypto",
    "joke", "poem", "story", "recipe"
]

# Match whole words only, so we don't block racing terms like "motorsport" or "history" just because they contain a blocked word.
_BLOCKED_TOPIC_PATTERN = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(topic) for topic in BLOCKED_TOPICS) + r")(?![\w-])"
)

RACING_KEYWORDS = [
    "lap", "sector", "tyre", "tire", "brake", "throttle", "gear",
    "speed", "corner", "pit", "overtake", "drs", "fuel", "stint",
    "understeer", "oversteer", "apex", "racing line", "time",
    "wheel", "spin", "rpm", "engineer", "strategy", "gap",
    "braking", "detected", "turn", "exit", "entry", "time loss"
]

# Use the same word-boundary check as _BLOCKED_TOPIC_PATTERN.
# A simple `in` or `\b` check can match "time" in "real-time", which can incorrectly trigger the racing keyword check.
_RACING_KEYWORD_PATTERN = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(kw) for kw in RACING_KEYWORDS) + r")(?![\w-])"
)

def validate_input(coaching_request: str):
    request_lower = coaching_request.lower()
    if _BLOCKED_TOPIC_PATTERN.search(request_lower):
        return False, "I can only provide racing coaching feedback. This request is not related to driving performance."
    has_racing_keyword = bool(_RACING_KEYWORD_PATTERN.search(request_lower))
    if not has_racing_keyword and len(coaching_request.split()) > 3:
        return False, "Please provide a racing-related coaching context."
    return True, None


# Output guardrail

MAX_WORDS = 30 # To prevent system generate feedback which is too long.

INVALID_PHRASES = [
    "i don't know",
    "i cannot",
    "can't provide",
    "cannot provide",
    "as an ai",
    "i'm not sure",
    "i am not able",
    "i'm unable",
    "without additional context",
    "without more context",
    "provide more details",
    "please note that",
    "it's important to note"
]

# Remove the model's unnecessary apology and keep the useful coaching advice.
_APOLOGY_LEAD = re.compile(
    r"""^\s*["']?\s*(i'?m\s+)?(sorry|apolog\w*)\b[^,.;:]*[,.;:]+\s*""",
    re.IGNORECASE,
)


def _strip_apology(text: str) -> str:
    return _APOLOGY_LEAD.sub("", text, count=1).strip().strip("\"'")


# Use simpler wording instead of "delta" or "time delta" so the feedback is easier for players to understand.
_DELTA_WORDING = re.compile(r"\btime\s+delta\b|\bdelta\b", re.IGNORECASE)


def _replace_delta_wording(text: str) -> str:
    return _DELTA_WORDING.sub("time loss", text)


# Supportive and aggressive styles don't need specific numbers. Remove any numbers the model adds.
_QUANTITY_UNIT_WORDS = {
    "km/h": "speed", "kmh": "speed", "kph": "speed", "mph": "speed", "m/s": "speed",
    "seconds": "time", "second": "time", "secs": "time", "sec": "time", "s": "time",
    "meters": "distance", "meter": "distance", "metres": "distance", "metre": "distance", "m": "distance",
    "%": "amount", "percent": "amount",
}

_QUANTITY_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(" + "|".join(re.escape(u) for u in _QUANTITY_UNIT_WORDS) + r")\b",
    re.IGNORECASE,
)


def _replace_quantity_wording(text: str) -> str:
    text = _QUANTITY_PATTERN.sub(lambda m: _QUANTITY_UNIT_WORDS[m.group(1).lower()], text)
    return re.sub(r"\s{2,}", " ", text).strip()


FALLBACK_RESPONSES = {
    "default": "Focus on braking points and consistent throttle application.",
    "late_braking": "Move your braking point earlier and trail brake into the apex.",
    "poor_corner_exit": "Apply throttle earlier and more progressively on corner exit.",
    "poor_track_position": "Follow the racing line and avoid large steering corrections.",
    "unstable_throttle": "Use one smooth throttle application instead of pumping the pedal.",
    "sector_time_loss": "Focus on the key corners in this sector to recover time.",
}

def validate_output(response: str, error_type: str = "default", style: str = "technical"):
    response = _replace_delta_wording(_strip_apology(response))
    if style in ("aggressive", "supportive"):
        response = _replace_quantity_wording(response)
    words = response.split()
    word_count = len(words)

    # Truncate first so the checks run on the text that will be spoken. Otherwise, invalid text may exist after truncation.
    if word_count > MAX_WORDS:
        candidate = ' '.join(words[:MAX_WORDS]).rstrip(',;') + '.'
    else:
        candidate = response
    candidate_lower = candidate.lower()

    # Only reject these phrases when there's no racing content in the response. This avoids rejecting useful coaching that happens to use them.
    has_concrete_detail = bool(re.search(r"\d", candidate)) or bool(
        _RACING_KEYWORD_PATTERN.search(candidate_lower)
    )
    if not has_concrete_detail:
        for phrase in INVALID_PHRASES:
            if phrase in candidate_lower:
                return False, FALLBACK_RESPONSES.get(error_type, FALLBACK_RESPONSES["default"])

    if word_count < 3:
        return False, FALLBACK_RESPONSES.get(error_type, FALLBACK_RESPONSES["default"])

    return True, candidate


# JSON output for UI team

def apply_guardrail(coaching_request: str, response: str, error: dict = None, style: str = "technical"):
    """
    Apply guardrails and return a JSON object for the UI team.

    Args:
        coaching_request: coaching context generated from Team 2's error report
        response: Granite's raw response
        error: optional error dict from error_detection.py
        style: coaching style in use ("aggressive"/"supportive"/"technical"),
            controls whether stray numbers get stripped from the response

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
    output_valid, cleaned_response = validate_output(response, error_type, style=style)

    return {
        "is_valid": output_valid,
        "feedback": cleaned_response,
        "error_type": error_type,
        "severity": severity,
        "corner": corner,
        "coaching_context": coaching_request
    }


def apply_guardrail_json(coaching_request: str, response: str, error: dict = None, style: str = "technical") -> str:
    """Same as apply_guardrail but returns a JSON string."""
    return json.dumps(apply_guardrail(coaching_request, response, error, style=style), indent=2)


def apply_guardrail_simple(question: str, response: str, style: str = "technical"):
    """Returns (is_valid, text) tuple for granite_adapter.py compatibility."""
    result = apply_guardrail(question, response, style=style)
    return result["is_valid"], result["feedback"]