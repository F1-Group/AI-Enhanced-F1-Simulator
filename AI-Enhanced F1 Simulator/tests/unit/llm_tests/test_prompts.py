import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "llm"))

import prompts


def _telemetry(**overrides):
    base = {
        "lap_distance": 1234.5,
        "speed_kmh": 210,
        "track_pos": 0.1,
        "angle": 2.0,
        "wheel_spin": 0.0,
        "lap_time": 78.4,
        "throttle": 0.6,
        "brake": 0.4,
        "steer": -0.2,
        "gear": 5,
        "rpm": 9500,
    }
    base.update(overrides)
    return base


def test_prompt_includes_every_telemetry_field():
    result = prompts.build_user_prompt(_telemetry(), "Late braking at Turn 3.")

    assert "1234.5m" in result
    assert "210 km/h" in result
    assert "0.1" in result
    assert "78.4s" in result
    assert "0.6" in result
    assert "0.4" in result
    assert "-0.2" in result
    assert "5" in result
    assert "9500" in result


def test_missing_telemetry_field_raises_key_error():
    incomplete = _telemetry()
    del incomplete["rpm"]

    try:
        prompts.build_user_prompt(incomplete, "Late braking.")
        assert False, "expected KeyError for missing telemetry field"
    except KeyError:
        pass


def test_knowledge_section_omitted_when_no_knowledge_passed():
    result = prompts.build_user_prompt(_telemetry(), "Poor corner exit.")

    assert "GENERAL BACKGROUND CONCEPT" not in result


def test_knowledge_section_present_and_labeled_as_illustrative_when_given():
    knowledge = "Trail braking reduces mid-corner speed loss."
    result = prompts.build_user_prompt(_telemetry(), "Poor corner exit.", knowledge=knowledge)

    assert "GENERAL BACKGROUND CONCEPT" in result
    assert knowledge in result


def test_coaching_context_is_embedded_verbatim():
    coaching_request = "Driver lost half a second in sector two."
    result = prompts.build_user_prompt(_telemetry(), coaching_request)

    assert f"COACHING CONTEXT: {coaching_request}" in result


def test_prompt_instructs_a_single_short_sentence_reply():
    # The rest of the pipeline (guardrail's MAX_WORDS, coaching styles) assumes
    # the model is told to keep replies terse - this is the one place that
    # instruction is actually issued to the LLM.
    result = prompts.build_user_prompt(_telemetry(), "Unstable throttle.")

    assert "ONE SENTENCE ONLY" in result
    assert "20 words" in result
