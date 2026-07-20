"""Fast layer: deterministic real-time checks on the latest telemetry frame.

These are the "Quick Judgment" if-else rules from the Team Responsibility
doc. They run on every frame (50 Hz) and must stay cheap: no pandas, just a
couple of numpy lookups against the precomputed expert baseline. Urgent
alerts go through the AudioManager, whose brake_now entry is configured to
interrupt any slow-layer coaching that is currently playing.
"""

import numpy as np
import time

from .lap_utils import SPEED_MAX_VALID

# How much faster than the expert (at the same point, while approaching a
# corner without braking) counts as "you must brake NOW".
OVERSPEED_KMH = 25.0
BRAKE_LOW = 0.30
APPROACH_ZONE_M = 150.0

# |track_pos| beyond this means the car is off the tarmac.
OFF_TRACK_POS = 1.0


class FastLayer:
    def __init__(self, baseline, corners, manager):
        self._dist = baseline["lap_distance"].to_numpy()
        self._speed = baseline["speed_kmh"].to_numpy()
        # (approach_start, apex) intervals for every corner
        self._zones = [(c["start_m"] - APPROACH_ZONE_M, c["apex_m"]) for c in corners]
        self._manager = manager
        self._last_fast_play = {}

    def _approaching_corner(self, d):
        return any(start <= d <= apex for start, apex in self._zones)

    def check(self, frame):
        """Run all fast rules on one telemetry frame (a plain dict).

        Returns the tag that fired, or None. The AudioManager's own cooldown
        stops one sustained condition from spamming audio every frame.
        """
        d = frame["lap_distance"]
        speed = frame["speed_kmh"]
        current_time = time.time()

        # Collisions spike the speed sensor past 1000 km/h (see lap_utils);
        # a glitched frame must never fire an urgent interrupt.
        if not 0.0 <= speed <= SPEED_MAX_VALID:
            return None

        if self._approaching_corner(d):
            expert_speed = float(np.interp(d, self._dist, self._speed))
            if speed > expert_speed + OVERSPEED_KMH and frame["brake"] < BRAKE_LOW:
                if current_time - self._last_fast_play.get("brake_now", 0.0) > 3.0:
                    print(f"\n[Fast Layer Feedback]: Brake now")
                    self._manager.play("brake_now")
                    self._last_fast_play["brake_now"] = current_time
                    return "brake_now"

        if abs(frame["track_pos"]) > OFF_TRACK_POS:
            if current_time - self._last_fast_play.get("off_track", 0.0) > 3.0:
                print(f"\n[Fast Layer Feedback]: You are off track")
                self._last_fast_play["off_track"] = current_time
                self._manager.play_error({
                    "tag": "off_track",
                    "type": "poor_track_position",
                    "audio_key": "off_track",
                    "audio_file": "audio/poor_track_position.wav",
                    "priority": "high",
                    "interrupt": True,
                })
                return "off_track"

        return None
