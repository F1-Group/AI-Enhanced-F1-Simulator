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
        "throttle": 0.0,
        "steer": 0.0,
        "gear": 3,
        "rpm": 7500.0,
    }
    data.update(overrides)
    return data


def test_wrong_way_requires_sustained_reverse_heading(monkeypatch):
    layer, manager, events = make_layer()
    clock = iter([10.0, 10.8, 11.6])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    assert layer.check(frame(angle=2.0, lap_distance=100.0)) is None
    assert layer.check(frame(angle=2.0, lap_distance=94.0)) is None
    assert layer.check(frame(angle=2.0, lap_distance=88.0)) == "wrong_way"
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
    clock = iter([25.0, 26.6])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    assert layer.check(frame(angle=0.0, gear=-1, speed_kmh=-30.0, lap_distance=100.0)) is None
    assert layer.check(frame(angle=0.0, gear=-1, speed_kmh=-30.0, lap_distance=88.0)) == "wrong_way"
    assert manager.played == ["wrong_way"]


def test_reversing_with_car_facing_backwards_moves_in_correct_direction(monkeypatch):
    layer, manager, _ = make_layer()
    clock = iter([27.0, 28.1])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    assert layer.check(frame(angle=3.0, gear=-1, speed_kmh=-30.0)) is None
    assert layer.check(frame(angle=3.0, gear=-1, speed_kmh=-30.0)) is None
    assert manager.played == []


def test_wrong_way_does_not_fire_without_reverse_track_progress(monkeypatch):
    layer, manager, _ = make_layer()
    clock = iter([29.0, 31.0])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    assert layer.check(frame(angle=2.0, speed_kmh=25.0, lap_distance=100.0)) is None
    assert layer.check(frame(angle=2.0, speed_kmh=25.0, lap_distance=99.0)) is None
    assert manager.played == []


def test_shift_up_alert(monkeypatch):
    layer, manager, events = make_layer()
    clock = iter([30.0, 30.7])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    assert layer.check(frame(gear=3, rpm=9000.0, throttle=1.0)) is None
    assert layer.check(frame(gear=3, rpm=9000.0, throttle=1.0)) == "shift_up"
    assert manager.played == ["shift_up"]
    assert events.get_nowait()["message"] == "Shift up."


def test_shift_down_alert(monkeypatch):
    layer, manager, events = make_layer()
    clock = iter([40.0, 40.7])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    candidate = frame(gear=4, rpm=4000.0, speed_kmh=200.0, brake=0.5)
    assert layer.check(candidate) is None
    assert layer.check(candidate) == "shift_down"
    assert manager.played == ["shift_down"]
    assert events.get_nowait()["message"] == "Shift down."


def test_shift_advice_ignores_reverse_neutral_and_stationary_car(monkeypatch):
    layer, manager, _ = make_layer()
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: 50.0)

    assert layer.check(frame(gear=-1, rpm=9500.0)) is None
    assert layer.check(frame(gear=0, rpm=9500.0)) is None
    assert layer.check(frame(gear=3, rpm=9500.0, speed_kmh=0.0)) is None
    assert manager.played == []


def test_downshift_is_suppressed_while_accelerating(monkeypatch):
    layer, manager, _ = make_layer()
    clock = iter([60.0, 61.0])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    unsafe = frame(gear=4, rpm=4000.0, speed_kmh=200.0, throttle=1.0)
    assert layer.check(unsafe) is None
    assert layer.check(unsafe) is None
    assert manager.played == []


def test_downshift_is_suppressed_while_car_is_unsettled(monkeypatch):
    layer, manager, _ = make_layer()
    clock = iter([70.0, 71.0, 72.0, 73.0])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    off_line = frame(gear=4, rpm=4000.0, brake=0.5, track_pos=0.95)
    turning = frame(gear=4, rpm=4000.0, brake=0.5, steer=0.4)
    assert layer.check(off_line) is None
    assert layer.check(off_line) is None
    assert layer.check(turning) is None
    assert layer.check(turning) is None
    assert manager.played == []


def test_only_one_downshift_per_continuous_braking_episode(monkeypatch):
    layer, manager, _ = make_layer()
    clock = iter([80.0, 80.7, 81.0, 82.0])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    braking_fourth = frame(gear=4, rpm=4000.0, speed_kmh=200.0, brake=0.5)
    braking_third = frame(gear=3, rpm=4000.0, speed_kmh=150.0, brake=0.5)
    assert layer.check(braking_fourth) is None
    assert layer.check(braking_fourth) == "shift_down"
    assert layer.check(braking_third) is None
    assert layer.check(braking_third) is None
    assert manager.played == ["shift_down"]


def test_unsafe_high_speed_downshift_is_suppressed(monkeypatch):
    layer, manager, _ = make_layer()
    clock = iter([90.0, 91.0])
    monkeypatch.setattr(fast_layer.time, "monotonic", lambda: next(clock))

    unsafe = frame(gear=2, rpm=4000.0, speed_kmh=265.0, brake=0.5)
    assert layer.check(unsafe) is None
    assert layer.check(unsafe) is None
    assert manager.played == []


def test_builtin_alert_messages_are_english():
    from audio_manager.audio_manager import BUILTIN_ALERTS

    assert BUILTIN_ALERTS["wrong_way"]["message"] == "Wrong way. Turn around."
    assert BUILTIN_ALERTS["shift_up"]["message"] == "Shift up."
    assert BUILTIN_ALERTS["shift_down"]["message"] == "Shift down."
    assert BUILTIN_ALERTS["shift_up"]["interrupt"] is True
    assert BUILTIN_ALERTS["shift_down"]["interrupt"] is True
