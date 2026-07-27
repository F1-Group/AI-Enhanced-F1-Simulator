import json
import re

# ─── INPUT GUARDRAIL ───────────────────────────────────────────────────────────

BLOCKED_TOPICS = [
    "politics", "food", "music", "movie", "sport",
    "football", "basketball", "cricket", "tennis",
    "relationship", "love", "crypto",
    "joke", "poem", "story", "recipe"
]

# Word-boundary match so racing terms that merely contain a blocked word as a
# substring (motorsport, gloves, history, ...) are not mistaken for the
# blocked topic itself. weather/stock/money were dropped from BLOCKED_TOPICS
# entirely - they're whole-word racing terms too (wet weather, stock car,
# money shift), not just substrings, so a word boundary alone can't save them.
# A plain \b treats "-" as a boundary, so "time" would still match inside
# "real-time". (?<![\w-]) / (?![\w-]) also excludes hyphenated compounds.
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

# Same word-boundary reasoning as _BLOCKED_TOPIC_PATTERN: a plain `in` check
# (and even a plain \b, which treats "-" as a boundary) lets "time" match
# inside "real-time", turning an unrelated word into a false racing-keyword
# hit.
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


# ─── OUTPUT GUARDRAIL ──────────────────────────────────────────────────────────

MAX_WORDS = 20

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

# The local model habitually opens with an apology despite the system prompt
# telling it not to. That opener carries no content, but the sentence after
# it is usually genuine, specific coaching advice - so strip the apology
# instead of discarding the whole response for a generic fallback.
_APOLOGY_LEAD = re.compile(
    r"""^\s*["']?\s*(i'?m\s+)?(sorry|apolog\w*)\b[^,.;:]*[,.;:]+\s*""",
    re.IGNORECASE,
)


def _strip_apology(text: str) -> str:
    return _APOLOGY_LEAD.sub("", text, count=1).strip().strip("\"'")

FALLBACK_RESPONSES = {
    "default": "Focus on braking points and consistent throttle application.",
    "late_braking": "Move your braking point earlier and trail brake into the apex.",
    "poor_corner_exit": "Apply throttle earlier and more progressively on corner exit.",
    "poor_track_position": "Follow the racing line and avoid large steering corrections.",
    "unstable_throttle": "Use one smooth throttle application instead of pumping the pedal.",
    "sector_time_loss": "Focus on the key corners in this sector to recover time.",
}

def validate_output(response: str, error_type: str = "default"):
    response = _strip_apology(response)
    words = response.split()
    word_count = len(words)

    # Truncate first so every check below judges the text that will actually
    # reach the driver - otherwise a rambling response that opens with a
    # refusal ("I cannot generate a response, as I am an AI...") but happens
    # to trail off into real numbers later can dodge the INVALID_PHRASES
    # check on the full text, only for MAX_WORDS to then cut those numbers
    # away and leave the refusal as the final spoken line.
    if word_count > MAX_WORDS:
        candidate = ' '.join(words[:MAX_WORDS]).rstrip(',;') + '.'
    else:
        candidate = response
    candidate_lower = candidate.lower()

    # A genuine refusal/hedge ("I'm not sure", "please note that", ...) never
    # references anything about the drive itself, while real coaching text
    # always does - a distance/speed number for aggressive/technical styles,
    # or at least racing vocabulary (turn, apex, braking, ...) for supportive
    # style, which is allowed to skip numbers. So only discard the response
    # for containing one of these phrases when it has neither - that's what
    # tells apart an actual non-answer from a coach who happens to open a
    # specific, useful sentence with "It's important to note that...".
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