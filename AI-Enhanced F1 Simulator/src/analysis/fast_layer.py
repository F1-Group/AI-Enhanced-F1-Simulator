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

# A car can briefly point across the track during a spin or recovery. Only
# announce wrong-way driving when it is moving forward while facing more than
# 90 degrees away from the track direction for a sustained period.
WRONG_WAY_ANGLE_RAD = np.pi / 2
WRONG_WAY_MIN_SPEED_KMH = 10
WRONG_WAY_CONFIRM_SECONDS = 1.5
WRONG_WAY_MIN_REVERSE_PROGRESS_M = 10.0

# TORCS reports engine RPM directly. These conservative thresholds sit near
# the useful upper/lower bounds observed in the supplied expert telemetry.
# They are kept here (rather than in the audio layer) so they can be tuned
# after play-testing without changing playback behaviour.
SHIFT_UP_RPM = 8800.0
SHIFT_DOWN_RPM = 4500.0
SHIFT_MIN_SPEED_KMH = 25.0
SHIFT_CONFIRM_SECONDS = 0.6
MAX_FORWARD_GEAR = 6
SHIFT_MAX_TRACK_POS = 0.90
SHIFT_MAX_ANGLE_RAD = 0.35
SHIFT_MAX_STEER = 0.25
SHIFT_UP_MIN_THROTTLE = 0.70
SHIFT_DOWN_MIN_BRAKE = 0.30
SHIFT_DOWN_MAX_THROTTLE = 0.05

# Prevent mechanically unsafe downshift advice at high road speed. Values are
# conservative limits derived from the supplied TORCS expert telemetry.
DOWNSHIFT_MAX_SPEED_KMH = {
    2: 50.0,
    3: 78.0,
    4: 100.0,
    5: 119.0,
    6: 133.0,
}


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
        self._wrong_way_since = None
        self._wrong_way_start_dist = None
        self._wrong_way_announced = False
        self._observed_gear = None
        self._shift_candidate = None
        self._shift_candidate_since = None
        self._shift_prompted_for_gear = False
        self._downshift_prompted_in_braking = False

    def _play_and_publish(self, tag, message, frame, priority="high"):
        """Speak a deterministic alert and mirror it to the dashboard queue.

        Queue events use high priority so ai_core treats them as Fast Layer
        feedback and does not send them through Granite or speak them again.
        AudioManager still owns the actual per-tag audio priority.
        """
        if self._manager:
            try:
                self._manager.play(tag)
            except Exception as exc:
                print(f"[Fast Layer Warning] {tag} audio failed: {exc}")

        if self._event_queue:
            try:
                self._event_queue.put_nowait({
                    "tag": tag,
                    "type": tag,
                    "priority": priority,
                    "interrupt": tag in {"wrong_way", "shift_up", "shift_down"},
                    "message": message,
                    "coaching_hint": message,
                    "telemetry": frame,
                })
            except Exception as exc:
                print(f"[Fast Layer Warning] {tag} event queue failed: {exc}")

    def _signed_track_delta(self, start_m, end_m):
        """Return shortest signed progress on the closed circuit."""
        if self.track_length <= 0:
            return end_m - start_m
        half = self.track_length / 2.0
        return (end_m - start_m + half) % self.track_length - half

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
        current_time = time.monotonic()

        # Collisions spike the speed sensor past 1000 km/h (see lap_utils).
        # Negative speed is legitimate while reversing, so validate the
        # magnitude here and handle reverse motion in the wrong-way rule.
        if abs(speed) > SPEED_MAX_VALID:
            return None

        gear = int(frame.get("gear", 0) or 0)
        rpm = float(frame.get("rpm", 0.0) or 0.0)
        angle = float(frame.get("angle", 0.0) or 0.0)
        throttle = float(frame.get("throttle", 0.0) or 0.0)
        current_brake = float(frame.get("brake", 0.0) or 0.0)
        steer = float(frame.get("steer", 0.0) or 0.0)
        track_pos = float(frame.get("track_pos", 0.0) or 0.0)

        # Releasing the brake starts a new braking episode. This prevents a
        # single braking zone from producing a chain of downshift prompts.
        if current_brake <= 0.10:
            self._downshift_prompted_in_braking = False

        # Wrong-way warning: require a sustained condition so a normal spin,
        # collision, or momentary sideways attitude does not trigger speech.
        # TORCS angle describes the car's heading relative to the track. When
        # reversing, actual travel direction is the opposite of that heading.
        reverse_motion = speed < -WRONG_WAY_MIN_SPEED_KMH or (
            gear < 0 and abs(speed) >= WRONG_WAY_MIN_SPEED_KMH
        )
        travel_angle = angle + (np.pi if reverse_motion else 0.0)
        travel_angle = (travel_angle + np.pi) % (2 * np.pi) - np.pi
        wrong_way = (
            abs(speed) >= WRONG_WAY_MIN_SPEED_KMH
            and abs(travel_angle) > WRONG_WAY_ANGLE_RAD
        )
        if wrong_way:
            if self._wrong_way_since is None:
                self._wrong_way_since = current_time
                self._wrong_way_start_dist = d
            reverse_progress = max(
                0.0,
                -self._signed_track_delta(self._wrong_way_start_dist, d),
            )
            confirmed = (
                current_time - self._wrong_way_since >= WRONG_WAY_CONFIRM_SECONDS
                and reverse_progress >= WRONG_WAY_MIN_REVERSE_PROGRESS_M
            )
            if confirmed and not self._wrong_way_announced:
                print("[Fast Layer] Wrong way - turn around")
                self._wrong_way_announced = True
                self._play_and_publish("wrong_way", "Wrong way. Turn around.", frame)
                return "wrong_way"
        else:
            self._wrong_way_since = None
            self._wrong_way_start_dist = None
            self._wrong_way_announced = False

        # The remaining rules assume forward longitudinal speed. Wrong-way
        # detection above is the only coaching rule applicable in reverse.
        if speed < 0.0:
            return None

        active_zone = self._get_current_corner_zone(d)
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
                # Audio intentionally disabled for off_track: detection and
                # the event-queue push (dashboard/logging) stay active below.
                if self._event_queue:
                    try:
                        self._event_queue.put_nowait(error_evt)
                    except Exception as e:
                        print(f"[Fast Layer Warning] Event queue put failed: {e}")
                return "off_track"

        # Gear coaching is evaluated after safety alerts. Only advise a shift
        # while the car is settled. Downshifts additionally require deliberate
        # braking with the throttle released.
        if wrong_way:
            return None

        if gear != self._observed_gear:
            self._observed_gear = gear
            self._shift_candidate = None
            self._shift_candidate_since = None
            self._shift_prompted_for_gear = False

        shift_tag = None
        stable_for_shift = (
            abs(track_pos) <= SHIFT_MAX_TRACK_POS
            and abs(angle) <= SHIFT_MAX_ANGLE_RAD
            and abs(steer) <= SHIFT_MAX_STEER
        )
        if (
            stable_for_shift
            and 1 <= gear < MAX_FORWARD_GEAR
            and speed >= SHIFT_MIN_SPEED_KMH
            and rpm >= SHIFT_UP_RPM
            and throttle >= SHIFT_UP_MIN_THROTTLE
            and current_brake <= 0.05
        ):
            shift_tag = "shift_up"
        elif (
            stable_for_shift
            and gear > 1
            and speed >= SHIFT_MIN_SPEED_KMH
            and 0.0 < rpm <= SHIFT_DOWN_RPM
            and speed <= DOWNSHIFT_MAX_SPEED_KMH.get(gear, float("inf"))
            and current_brake >= SHIFT_DOWN_MIN_BRAKE
            and throttle <= SHIFT_DOWN_MAX_THROTTLE
            and not self._downshift_prompted_in_braking
        ):
            shift_tag = "shift_down"

        if shift_tag != self._shift_candidate:
            self._shift_candidate = shift_tag
            self._shift_candidate_since = current_time if shift_tag else None

        if shift_tag and not self._shift_prompted_for_gear:
            stable = current_time - self._shift_candidate_since >= SHIFT_CONFIRM_SECONDS
            if stable:
                shift_message = "Shift up." if shift_tag == "shift_up" else "Shift down."
                print(f"[Fast Layer] {shift_message}")
                self._shift_prompted_for_gear = True
                if shift_tag == "shift_down":
                    self._downshift_prompted_in_braking = True
                self._play_and_publish(shift_tag, shift_message, frame)
                return shift_tag

        return None
