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
    def __init__(self, baseline, corners, manager, event_queue=None):
        self._dist = baseline["lap_distance"].to_numpy()
        self._speed = baseline["speed_kmh"].to_numpy()
        self.track_length = float(self._dist.max()) if len(self._dist) > 0 else 6283.0

        self._corners_raw = corners
        self._zones = []

        for c in corners:
            start_m = c.get("start_m", c.get("start", c.get("entry", 0.0)))
            apex_m = c.get("apex_m", c.get("apex", start_m + 50.0))
            zone_start = start_m - APPROACH_ZONE_M
            zone_end = max(apex_m, start_m + 10.0)

            self._zones.append((zone_start, zone_end, c.get("name", "unknown")))

        self._manager = manager
        self._last_fast_play = {}
        self._event_queue = event_queue

    def _get_current_corner_zone(self, d):
        candidate_zones = []
        for start, end, name in self._zones:
            # Normal zone
            if start >= 0:
                if start <= d <= end:
                    candidate_zones.append((start, end, name))
            # Zone that wraps around the start/finish line
            else:
                wrapped_start = self.track_length + start
                if d >= wrapped_start or d <= end:
                    candidate_zones.append((start, end, name))

        if not candidate_zones:
            return None

        # Only one zone matched, return it directly
        if len(candidate_zones) == 1:
            return candidate_zones[0]

        # Multiple overlapping zones (e.g. an S-curve / consecutive corners):
        # pick the corner whose apex is closest ahead of the current position
        best_zone = candidate_zones[0]
        min_dist_to_apex = float("inf")

        for start, end, name in candidate_zones:
            # Look up this corner's apex_m
            apex_m = end  # fallback default
            for c in self._corners_raw:
                if c.get("name") == name:
                    apex_m = c.get("apex_m", c.get("apex", end))
                    break

            # Distance remaining to the apex
            dist_to_apex = apex_m - d
            if dist_to_apex < 0:
                continue
            if dist_to_apex < min_dist_to_apex:
                min_dist_to_apex = dist_to_apex
                best_zone = (start, end, name)

        return best_zone

    def check(self, frame):
        """Run all fast rules on one telemetry frame (a plain dict).

        Returns the tag that fired, or None. The AudioManager's own cooldown
        stops one sustained condition from spamming audio every frame.
        """
        d = frame.get("lap_distance", 0.0)
        speed = frame.get("speed_kmh", 0.0)
        current_time = time.time()

        # Collisions spike the speed sensor past 1000 km/h (see lap_utils);
        # a glitched frame must never fire an urgent interrupt.
        if not 0.0 <= speed <= SPEED_MAX_VALID:
            return None

        active_zone = self._get_current_corner_zone(d)
        current_brake = frame.get("brake", 0.0)

        if active_zone is not None:
            _, _, c_name = active_zone

            # Expert's speed at the player's current distance
            expert_curr_speed = float(np.interp(d, self._dist, self._speed))

            speed_cond = speed > (expert_curr_speed + OVERSPEED_KMH)
            brake_cond = current_brake < BRAKE_LOW

            if speed_cond and brake_cond:
                if current_time - self._last_fast_play.get("brake_now", 0.0) > 3.0:
                    print(f"[Fast Layer] Brake NOW! ({c_name})")
                    if self._manager:
                        try:
                            self._manager.play("brake_now")
                        except Exception as e:
                            print(f"[Fast Layer Warning] Audio play failed: {e}")

                    self._last_fast_play["brake_now"] = current_time

                    if self._event_queue:
                        try:
                            self._event_queue.put_nowait({
                                "type": "brake_now",
                                "priority": "high",
                                "message": "Brake NOW!",
                                "coaching_hint": "Brake NOW!",
                                "telemetry": frame,
                            })
                        except Exception as e:
                            print(f"[Fast Layer Warning] Event queue put failed: {e}")
                    return "brake_now"

        # Off track check
        if abs(frame.get("track_pos", 0.0)) > OFF_TRACK_POS:
            if current_time - self._last_fast_play.get("off_track", 0.0) > 3.0:
                print("[Fast Layer] You are off track")
                self._last_fast_play["off_track"] = current_time
                error_evt = {
                    "tag": "off_track",
                    "type": "poor_track_position",
                    "audio_key": "off_track",
                    "audio_file": "audio/poor_track_position.wav",
                    "priority": "high",
                    "message": "You are off track!",
                    "coaching_hint": "You are off track!",
                    "interrupt": True,
                }
                if self._manager:
                    try:
                        self._manager.play_error(error_evt)
                    except Exception as e:
                        print(f"[Fast Layer Warning] Off track audio play failed: {e}")

                if self._event_queue:
                    try:
                        self._event_queue.put_nowait(error_evt)
                    except Exception as e:
                        print(f"[Fast Layer Warning] Event queue put failed: {e}")
                return "off_track"

        return None