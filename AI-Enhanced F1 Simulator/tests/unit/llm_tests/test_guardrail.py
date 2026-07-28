import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "llm"))

import guardrail


# ─── validate_input ──────────────────────────────────────────────────────────

def test_standalone_blocked_topic_is_rejected():
    valid, message = guardrail.validate_input("Tell me a joke.")

    assert valid is False
    assert "racing coaching" in message


def test_blocked_topic_as_substring_of_a_racing_word_is_not_blocked():
    # "sport" is blocked, but it must not fire just because it appears inside
    # "motorsport" - the word-boundary regex is what prevents that.
    valid, message = guardrail.validate_input("This motorsport strategy needs work.")

    assert valid is True
    assert message is None


def test_hyphenated_word_does_not_match_racing_keyword_substring():
    # "time" is a racing keyword, but "real-time" must not match it (the
    # pattern excludes hyphenated compounds), so this falls through to the
    # non-racing rejection instead of being treated as racing-related.
    valid, message = guardrail.validate_input("Give me a real-time analysis please.")

    assert valid is False
    assert message == "Please provide a racing-related coaching context."


def test_short_non_racing_request_is_allowed():
    # <= 3 words is allowed through even without a racing keyword.
    valid, message = guardrail.validate_input("Nice work today")

    assert valid is True
    assert message is None


def test_long_non_racing_request_without_keyword_is_rejected():
    valid, message = guardrail.validate_input("Please tell me something interesting today")

    assert valid is False
    assert message == "Please provide a racing-related coaching context."


def test_racing_keyword_present_allows_a_long_request():
    valid, message = guardrail.validate_input(
        "I really want to understand why my braking point keeps drifting late every lap"
    )

    assert valid is True
    assert message is None


# ─── validate_output ─────────────────────────────────────────────────────────

def test_apology_lead_is_stripped_but_real_advice_is_kept():
    valid, text = guardrail.validate_output(
        "Sorry, brake ten meters earlier into turn 3.", "late_braking"
    )

    assert valid is True
    assert "sorry" not in text.lower()
    assert "brake ten meters earlier" in text.lower()


def test_turn_number_is_normalized_to_corner():
    valid, text = guardrail.validate_output("Brake earlier into Turn 5.", "late_braking")

    assert valid is True
    assert "Turn 5" not in text
    assert "the corner" in text.lower()


def test_response_over_max_words_is_truncated_with_trailing_period():
    words = ["brake"] + ["word"] * 49
    response = " ".join(words)

    valid, text = guardrail.validate_output(response, "default")

    assert valid is True
    assert text.endswith(".")
    assert len(text.rstrip(".").split()) == guardrail.MAX_WORDS


def test_invalid_phrase_without_concrete_detail_falls_back():
    response = "I'm not sure what to tell you honestly speaking here today"

    valid, text = guardrail.validate_output(response, "late_braking")

    assert valid is False
    assert text == guardrail.FALLBACK_RESPONSES["late_braking"]


def test_invalid_phrase_is_kept_when_response_has_concrete_detail():
    # Same hedging phrase as above, but this time the sentence also carries a
    # number and racing vocabulary, so it must be treated as real advice
    # rather than discarded as a non-answer.
    response = "I'm not sure but brake 10 meters earlier into the apex."

    valid, text = guardrail.validate_output(response, "late_braking")

    assert valid is True
    assert text == response


def test_short_response_falls_back():
    valid, text = guardrail.validate_output("Brake earlier.", "late_braking")

    assert valid is False
    assert text == guardrail.FALLBACK_RESPONSES["late_braking"]


def test_unknown_error_type_uses_default_fallback():
    valid, text = guardrail.validate_output("no", "totally_unknown_type")

    assert valid is False
    assert text == guardrail.FALLBACK_RESPONSES["default"]


# ─── apply_guardrail / json / simple wrappers ────────────────────────────────

def test_apply_guardrail_blocks_non_racing_topic_before_looking_at_the_response():
    result = guardrail.apply_guardrail("Tell me a joke", "anything the LLM said")

    assert result["is_valid"] is False
    assert result["error_type"] is None
    assert result["corner"] is None
    assert result["coaching_context"] == "Tell me a joke"


def test_apply_guardrail_passes_through_error_metadata_when_valid():
    error = {"type": "late_braking", "corner": "Turn 3", "severity": "high"}
    result = guardrail.apply_guardrail(
        "Late braking at turn 3", "Brake ten meters earlier into the apex.", error=error
    )

    assert result["is_valid"] is True
    assert result["error_type"] == "late_braking"
    assert result["corner"] == "Turn 3"
    assert result["severity"] == "high"


def test_apply_guardrail_defaults_error_type_when_no_error_dict_given():
    result = guardrail.apply_guardrail(
        "Brake earlier into the apex", "Brake ten meters earlier into the apex."
    )

    assert result["error_type"] == "default"
    assert result["corner"] is None
    assert result["severity"] is None


def test_apply_guardrail_json_matches_the_dict_form():
    error = {"type": "late_braking", "corner": "Turn 3", "severity": "high"}
    expected = guardrail.apply_guardrail(
        "Late braking at turn 3", "Brake ten meters earlier into the apex.", error=error
    )
    text = guardrail.apply_guardrail_json(
        "Late braking at turn 3", "Brake ten meters earlier into the apex.", error=error
    )

    assert json.loads(text) == expected


def test_apply_guardrail_simple_returns_is_valid_feedback_tuple():
    is_valid, text = guardrail.apply_guardrail_simple(
        "Brake earlier into the apex", "Brake ten meters earlier into the apex."
    )

    assert is_valid is True
    assert text == "Brake ten meters earlier into the apex."
