import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "llm"))

import ai_core


class _FakeAudioManager:
    def __init__(self):
        self.played = []

    def play_text(self, text, priority="normal", cooldown_key=None):
        self.played.append((text, priority, cooldown_key))


def _make_error(error_type="late_braking", corner="Turn 3"):
    return {
        "type": error_type,
        "corner": corner,
        "severity": "normal",
        "message": f"Late braking detected at {corner}.",
        "coaching_hint": "Brake earlier and trail brake into the apex.",
        "telemetry": {
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
        },
    }


@pytest.fixture(autouse=True)
def reset_breaker():
    ai_core._reset_llm_breaker()
    yield
    ai_core._reset_llm_breaker()


@pytest.fixture
def no_rag(monkeypatch):
    # Isolate breaker behavior from the real (slow) RAG pipeline - only
    # ask_race_engineer's success/failure should matter for these tests.
    monkeypatch.setattr(ai_core, "retrieve", lambda query, top_k=2: ["Brake earlier into apex corners."])


def test_breaker_is_closed_and_calls_llm_when_healthy(monkeypatch, no_rag):
    calls = []

    def fake_ask(system_prompt, user_prompt):
        calls.append((system_prompt, user_prompt))
        return "Brake ten meters earlier and trail brake to the apex."

    monkeypatch.setattr(ai_core, "ask_race_engineer", fake_ask)
    audio = _FakeAudioManager()

    result = ai_core.process_single_error(_make_error(), "system prompt", audio)

    assert len(calls) == 1
    assert not ai_core._llm_breaker_is_open()
    assert result["is_valid"] is True
    assert audio.played


def test_first_failure_trips_breaker_and_pays_full_cost(monkeypatch, no_rag):
    def fake_ask(system_prompt, user_prompt):
        raise ConnectionError("ollama unavailable")

    monkeypatch.setattr(ai_core, "ask_race_engineer", fake_ask)
    audio = _FakeAudioManager()

    assert not ai_core._llm_breaker_is_open()
    result = ai_core.process_single_error(_make_error(), "system prompt", audio)

    assert ai_core._llm_breaker_is_open()
    assert result["feedback"] == ai_core.get_fallback_text("late_braking")


def test_later_errors_skip_the_llm_and_fall_back_quickly_after_trip(monkeypatch, no_rag):
    calls = []

    def fake_ask(system_prompt, user_prompt):
        calls.append(1)
        raise ConnectionError("ollama unavailable")

    monkeypatch.setattr(ai_core, "ask_race_engineer", fake_ask)
    audio = _FakeAudioManager()

    ai_core.process_single_error(_make_error(), "system prompt", audio)
    assert len(calls) == 1

    start = time.time()
    result = ai_core.process_single_error(_make_error(corner="Turn 5"), "system prompt", audio)
    elapsed = time.time() - start

    # Breaker is open: the second call must skip the LLM entirely.
    assert len(calls) == 1
    assert elapsed < 0.2
    assert result["feedback"] == ai_core.get_fallback_text("late_braking")


def test_breaker_auto_resets_and_probes_llm_again_after_cooldown(monkeypatch, no_rag):
    calls = []

    def fake_ask(system_prompt, user_prompt):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("ollama unavailable")
        return "Good recovery, keep the same braking point next lap."

    monkeypatch.setattr(ai_core, "ask_race_engineer", fake_ask)
    audio = _FakeAudioManager()

    ai_core.process_single_error(_make_error(), "system prompt", audio)
    assert ai_core._llm_breaker_is_open()

    # Fast-forward past the cooldown instead of sleeping in the test.
    ai_core._llm_retry_at = time.time() - 0.01

    result = ai_core.process_single_error(_make_error(corner="Turn 6"), "system prompt", audio)

    assert len(calls) == 2
    assert not ai_core._llm_breaker_is_open()
    assert result["is_valid"] is True


def test_force_fallback_skips_llm_regardless_of_breaker_state(monkeypatch, no_rag):
    calls = []
    monkeypatch.setattr(ai_core, "ask_race_engineer", lambda *a, **k: calls.append(1))
    audio = _FakeAudioManager()

    assert not ai_core._llm_breaker_is_open()
    result = ai_core.process_single_error(_make_error(), "system prompt", audio, force_fallback=True)

    assert calls == []
    assert result["feedback"] == ai_core.get_fallback_text("late_braking")


def test_end_to_end_real_ask_race_engineer_failure_trips_the_breaker(monkeypatch, no_rag):
    # Exercise the real ask_race_engineer (imported from llm_client) instead of
    # stubbing it directly, so the breaker is proven to trip off a genuine
    # failure surfaced through the full call path, not just a mocked function.
    # ai_core imports it as `llm.llm_client` (it puts src/ on sys.path itself),
    # so we must patch that same module object - a plain `import llm_client`
    # here would create a second, unrelated module instance.
    from llm import llm_client

    class _FailingOllamaClient:
        def chat(self, model, messages):
            raise ConnectionError("local Ollama server is not running")

    monkeypatch.setattr(llm_client, "_client", _FailingOllamaClient())
    audio = _FakeAudioManager()

    assert not ai_core._llm_breaker_is_open()
    result = ai_core.process_single_error(_make_error(), "system prompt", audio)

    assert ai_core._llm_breaker_is_open()
    assert result["feedback"] == ai_core.get_fallback_text("late_braking")
