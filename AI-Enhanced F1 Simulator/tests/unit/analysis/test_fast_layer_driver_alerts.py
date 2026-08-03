import queue
import sys
from pathlib import Path

import pandas as pd

SRC_DIR = Path(__file__).resolve().parents[3] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from analysis import fast_layer


class FakeAudioManager:
    def __init__(self):
        self.played = []

    def play(self, tag):
        self.played.append(tag)
        return True


def make_layer():
    baseline = pd.DataFrame({
        "lap_distance": [0.0, 100.0, 200.0],
        "speed_kmh": [100.0, 100.0, 100.0],
    })
    manager = FakeAudioManager()
    events = queue.Queue()
    return fast_layer.FastLayer(baseline, [], manager, events), manager, events


def frame(**overrides):
    data = {
        "lap_distance": 100.0,
        "speed_kmh": 80.0,
        "track_pos": 0.0,
        "angle": 0.0,
        "brake": 0.0,
        "gear": 3,
        "rpm": 7500.0,
    }
    data.update(overrides)
    return data


def test_wrong_way_requires_sustained_reverse_heading(monkeypatch):
    layer, manager, events = make_layer()
    clock = iter([10.0, 10.5, 11.1])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    assert layer.check(frame(angle=2.0)) is None
    assert layer.check(frame(angle=2.0)) is None
    assert layer.check(frame(angle=2.0)) == "wrong_way"
    assert manager.played == ["wrong_way"]
    assert events.get_nowait()["message"] == "Wrong way. Turn around."


def test_wrong_way_timer_resets_when_heading_recovers(monkeypatch):
    layer, manager, _ = make_layer()
    clock = iter([20.0, 20.8, 21.0, 21.5])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    assert layer.check(frame(angle=2.0)) is None
    assert layer.check(frame(angle=0.2)) is None
    assert layer.check(frame(angle=2.0)) is None
    assert layer.check(frame(angle=2.0)) is None
    assert manager.played == []


def test_reversing_against_track_direction_is_wrong_way(monkeypatch):
    layer, manager, _ = make_layer()
    clock = iter([25.0, 26.1])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    assert layer.check(frame(angle=0.0, gear=-1, speed_kmh=-20.0)) is None
    assert layer.check(frame(angle=0.0, gear=-1, speed_kmh=-20.0)) == "wrong_way"
    assert manager.played == ["wrong_way"]


def test_reversing_with_car_facing_backwards_moves_in_correct_direction(monkeypatch):
    layer, manager, _ = make_layer()
    clock = iter([27.0, 28.1])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    assert layer.check(frame(angle=3.0, gear=-1, speed_kmh=-20.0)) is None
    assert layer.check(frame(angle=3.0, gear=-1, speed_kmh=-20.0)) is None
    assert manager.played == []


def test_shift_up_alert(monkeypatch):
    layer, manager, events = make_layer()
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: 30.0)

    assert layer.check(frame(gear=3, rpm=9200.0)) == "shift_up"
    assert manager.played == ["shift_up"]
    assert events.get_nowait()["message"] == "Shift up."


def test_shift_down_alert(monkeypatch):
    layer, manager, events = make_layer()
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: 40.0)

    assert layer.check(frame(gear=4, rpm=5800.0)) == "shift_down"
    assert manager.played == ["shift_down"]
    assert events.get_nowait()["message"] == "Shift down."


def test_shift_advice_ignores_reverse_neutral_and_stationary_car(monkeypatch):
    layer, manager, _ = make_layer()
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: 50.0)

    assert layer.check(frame(gear=-1, rpm=9500.0)) is None
    assert layer.check(frame(gear=0, rpm=9500.0)) is None
    assert layer.check(frame(gear=3, rpm=9500.0, speed_kmh=0.0)) is None
    assert manager.played == []


def test_builtin_alert_messages_are_english():
    from audio_manager.audio_manager import BUILTIN_ALERTS

    assert BUILTIN_ALERTS["wrong_way"]["message"] == "Wrong way. Turn around."
    assert BUILTIN_ALERTS["shift_up"]["message"] == "Shift up."
    assert BUILTIN_ALERTS["shift_down"]["message"] == "Shift down."
