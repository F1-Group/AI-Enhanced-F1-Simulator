import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "llm"))

import llm_client


class _FakeOllamaClient:
    """Stand-in for ollama.Client that lets tests script delay/success/failure
    per call instead of hitting a real local Ollama server."""

    def __init__(self, behaviors):
        self._behaviors = list(behaviors)
        self.calls = 0

    def chat(self, model, messages):
        self.calls += 1
        behavior = self._behaviors.pop(0)
        return behavior()


def _ok(content="Brake ten meters earlier into turn one."):
    def _behavior():
        return {"message": {"content": content}}
    return _behavior


def _fail(exc=None):
    def _behavior():
        raise (exc or ConnectionError("ollama backend unavailable"))
    return _behavior


def _slow(delay, content="ok"):
    def _behavior():
        time.sleep(delay)
        return {"message": {"content": content}}
    return _behavior


@pytest.fixture
def fake_client(monkeypatch):
    def _install(behaviors):
        fake = _FakeOllamaClient(behaviors)
        monkeypatch.setattr(llm_client, "_client", fake)
        return fake
    return _install


def test_llm_client_uses_a_bounded_timeout():
    # A None timeout would let ollama's client hang forever if the local
    # server stalls - it must be a positive, finite number of seconds.
    assert llm_client.REQUEST_TIMEOUT_SECONDS is not None
    assert 0 < llm_client.REQUEST_TIMEOUT_SECONDS < 60


def test_client_side_timeout_bounds_a_hung_backend(fake_client):
    # A hung backend surfaces as ollama raising once its client-side timeout
    # elapses; ask_race_engineer must propagate that instead of blocking.
    timeout_error = TimeoutError(f"timed out after {llm_client.REQUEST_TIMEOUT_SECONDS}s")
    fake_client([_fail(timeout_error)])

    start = time.time()
    with pytest.raises(TimeoutError):
        llm_client.ask_race_engineer("system", "user", max_retries=1)
    assert time.time() - start < 1.0


def test_default_max_retries_bounds_worst_case_detection_time(fake_client):
    fake = fake_client([_fail()])
    start = time.time()
    with pytest.raises(Exception):
        llm_client.ask_race_engineer("system", "user")
    elapsed = time.time() - start

    assert fake.calls == 1
    # Default max_retries=1 means no retry sleep, so a failure is detected
    # almost immediately rather than after a wait_seconds delay.
    assert elapsed < 0.5


def test_immediate_success_has_near_zero_delay(fake_client):
    fake_client([_ok("Lift and coast into turn four.")])

    start = time.time()
    result = llm_client.ask_race_engineer("system", "user")
    elapsed = time.time() - start

    assert result == "Lift and coast into turn four."
    assert elapsed < 0.2


def test_retry_triggered_delay_matches_wait_seconds(fake_client):
    fake = fake_client([_fail(), _ok("Better exit next lap.")])

    start = time.time()
    result = llm_client.ask_race_engineer("system", "user", max_retries=2, wait_seconds=0.3)
    elapsed = time.time() - start

    assert result == "Better exit next lap."
    assert fake.calls == 2
    assert 0.3 <= elapsed < 0.6


def test_all_attempts_failing_raises_within_expected_bound(fake_client):
    fake = fake_client([_fail(), _fail(), _fail()])

    start = time.time()
    with pytest.raises(Exception):
        llm_client.ask_race_engineer("system", "user", max_retries=3, wait_seconds=0.2)
    elapsed = time.time() - start

    assert fake.calls == 3
    # Two sleeps between three attempts.
    assert 0.4 <= elapsed < 0.8


def test_all_attempts_failing_falls_back_within_expected_bound(fake_client):
    fake_client([_fail(), _fail()])

    start = time.time()
    with pytest.raises(Exception):
        llm_client.ask_race_engineer("system", "user", max_retries=2, wait_seconds=0.1)
    fallback = llm_client.get_fallback_text("late_braking")
    elapsed = time.time() - start

    assert fallback == llm_client.FALLBACK_SCRIPTS["late_braking"]
    assert elapsed < 0.3


def test_slow_backend_does_not_block_past_a_reasonable_ceiling(fake_client):
    fake_client([_slow(0.1, "Steady throttle out of the last corner.")])

    start = time.time()
    result = llm_client.ask_race_engineer("system", "user")
    elapsed = time.time() - start

    assert result == "Steady throttle out of the last corner."
    assert elapsed < llm_client.REQUEST_TIMEOUT_SECONDS


def test_reply_time_distribution(fake_client):
    delays = [0.01, 0.02, 0.03]
    fake_client([_slow(d, f"tip {i}") for i, d in enumerate(delays)])

    elapsed_times = []
    for _ in delays:
        start = time.time()
        llm_client.ask_race_engineer("system", "user")
        elapsed_times.append(time.time() - start)

    assert len(elapsed_times) == len(delays)
    assert all(e < 1.0 for e in elapsed_times)
    assert max(elapsed_times) >= min(delays)
