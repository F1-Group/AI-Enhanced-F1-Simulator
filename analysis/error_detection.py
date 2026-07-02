"""Turn aligned player-vs-expert deltas into error JSON for Team 3 / audio.

The output objects follow the same schema as mock/error_template.json, so
AudioManager.play_error() and Team 3's Granite prompt builder can consume
them without changes. An extra "evidence" block carries the measured numbers
so Granite can reference concrete telemetry in its coaching text.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

# mock/error_template.json is the cross-team contract: it owns each error
# type's layer/priority/interrupt/audio so Team 3 and the AudioManager can
# tune them in one place. Detection thresholds stay in this module.
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "mock" / "error_template.json"


def _load_type_defaults():
    try:
        template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    defaults = {}
    for entry in template.get("errors", []):
        defaults.setdefault(entry["type"], entry)
    return defaults


_TYPE_DEFAULTS = _load_type_defaults()

# --- Corner detection -------------------------------------------------------

CORNER_STEER_THRESHOLD = 0.10   # |steer| above this counts as cornering
CORNER_MIN_LENGTH_M = 30.0      # ignore tiny steering corrections
CORNER_MERGE_GAP_M = 60.0       # merge segments separated by short straights
ENTRY_ZONE_M = 150.0            # braking zone checked before corner start
EXIT_ZONE_M = 150.0             # traction zone checked after corner end

# --- Detection thresholds ----------------------------------------------------

BRAKE_ON = 0.30                 # pedal position that counts as "braking"
LATE_BRAKING_M = 25.0           # braking this many metres later than expert
BRAKE_SEARCH_ZONE_M = 300.0     # how far before the corner to look for braking
ENTRY_OVERSPEED_KMH = 15.0
EXIT_SPEED_DEFICIT_KMH = 12.0
OFFLINE_TRACK_POS = 0.35        # mean |track_pos| difference inside corner
THROTTLE_STD_EXCESS = 0.15      # player throttle jitter above expert's
SECTOR_TIME_LOSS_S = 2.0
MAX_ALIGN_GAP_M = 20.0          # grid points with worse alignment are ignored


def detect_corners(baseline):
    """Number the track's corners (T1, T2, ...) from the expert steer trace."""
    steer = np.abs(baseline["steer"].to_numpy())
    # Light smoothing so single noisy samples don't split a corner in two.
    steer = pd.Series(steer).rolling(5, center=True, min_periods=1).mean().to_numpy()
    dist = baseline["lap_distance"].to_numpy()

    in_corner = steer > CORNER_STEER_THRESHOLD
    segments = []
    start = None
    for i, flag in enumerate(in_corner):
        if flag and start is None:
            start = dist[i]
        elif not flag and start is not None:
            segments.append([start, dist[i]])
            start = None
    if start is not None:
        segments.append([start, dist[-1]])

    merged = []
    for seg in segments:
        if merged and seg[0] - merged[-1][1] < CORNER_MERGE_GAP_M:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)

    corners = []
    for n, (s, e) in enumerate(seg for seg in merged if seg[1] - seg[0] >= CORNER_MIN_LENGTH_M):
        zone = (baseline["lap_distance"] >= s) & (baseline["lap_distance"] <= e)
        apex = float(baseline.loc[zone, "lap_distance"][baseline.loc[zone, "speed_kmh"].idxmin()])
        corners.append({"name": f"T{n + 1}", "start_m": float(s), "end_m": float(e), "apex_m": apex})
    return corners


# --- Error rules -------------------------------------------------------------


def _zone(deltas, start_m, end_m):
    mask = (deltas["lap_distance"] >= start_m) & (deltas["lap_distance"] <= end_m)
    zone = deltas[mask]
    return zone[zone["align_gap_m"] <= MAX_ALIGN_GAP_M]


def _confidence(magnitude, threshold):
    """Scale confidence with how far past the threshold the measurement is."""
    return round(float(np.clip(0.6 + 0.3 * (magnitude / threshold - 1.0), 0.6, 0.95)), 2)


def _severity(magnitude, threshold):
    ratio = magnitude / threshold
    if ratio >= 2.0:
        return "high"
    if ratio >= 1.4:
        return "medium"
    return "low"


def _first_brake_point(zone, brake_col):
    braking = zone[zone[brake_col] >= BRAKE_ON]
    return float(braking["lap_distance"].iloc[0]) if len(braking) else None


def _make_error(tag, corner, error_type, severity, confidence, metrics, message, hint, evidence):
    defaults = _TYPE_DEFAULTS.get(error_type, {})
    return {
        "tag": tag,
        "layer": defaults.get("layer", "slow"),
        "priority": defaults.get("priority", "normal"),
        "interrupt": defaults.get("interrupt", False),
        "corner": corner,
        "type": error_type,
        "severity": severity,
        "confidence": confidence,
        "related_metrics_granite": metrics,
        "message": message,
        "coaching_hint": hint,
        # Audio is shared per error type (audio/late_braking.wav etc.), so the
        # AudioManager fallback works for any corner without one file per tag.
        "audio_key": defaults.get("audio_key", error_type),
        "audio_file": defaults.get("audio_file", f"audio/{error_type}.wav"),
        "evidence": evidence,
    }


def detect_corner_errors(deltas, corners):
    errors = []
    for corner in corners:
        name = corner["name"]
        entry = _zone(deltas, corner["start_m"] - ENTRY_ZONE_M, corner["apex_m"])
        mid = _zone(deltas, corner["start_m"], corner["end_m"])
        exit_ = _zone(deltas, corner["end_m"], corner["end_m"] + EXIT_ZONE_M)
        if entry.empty or mid.empty or exit_.empty:
            continue

        # Late braking. The brake search zone is wider than the entry zone so
        # a player who brakes *earlier* than the expert is never misread as
        # not braking at all (that is good driving, not an error).
        brake_zone = _zone(deltas, corner["start_m"] - BRAKE_SEARCH_ZONE_M, corner["apex_m"])
        expert_bp = _first_brake_point(brake_zone, "expert_brake")
        player_bp = _first_brake_point(brake_zone, "player_brake")
        entry_overspeed = float(entry["delta_speed_kmh"].max())
        if expert_bp is not None and player_bp is not None:
            late_by = player_bp - expert_bp
            if late_by >= LATE_BRAKING_M and entry_overspeed >= ENTRY_OVERSPEED_KMH / 2:
                errors.append(_make_error(
                    f"{name}_late_braking", name, "late_braking",
                    _severity(late_by, LATE_BRAKING_M), _confidence(late_by, LATE_BRAKING_M),
                    ["brake", "speed_kmh", "lap_distance"],
                    f"Late braking detected at {name}.",
                    f"Brake about {late_by:.0f} m earlier before {name} to stabilise corner entry.",
                    {
                        "expert_brake_point_m": expert_bp,
                        "player_brake_point_m": player_bp,
                        "braked_late_by_m": round(late_by, 1),
                        "entry_overspeed_kmh": round(entry_overspeed, 1),
                    },
                ))
        elif expert_bp is not None and entry_overspeed >= ENTRY_OVERSPEED_KMH:
            # Player never braked firmly anywhere in the approach AND is
            # arriving clearly too fast; severity scales with the overspeed
            # (a measured value), not a made-up lateness distance.
            errors.append(_make_error(
                f"{name}_late_braking", name, "late_braking",
                _severity(entry_overspeed, ENTRY_OVERSPEED_KMH),
                _confidence(entry_overspeed, ENTRY_OVERSPEED_KMH),
                ["brake", "speed_kmh", "lap_distance"],
                f"No braking detected before {name}.",
                f"You entered {name} about {entry_overspeed:.0f} km/h faster than the "
                f"baseline without braking; the baseline brakes about "
                f"{corner['apex_m'] - expert_bp:.0f} m before the apex.",
                {
                    "expert_brake_point_m": expert_bp,
                    "player_brake_point_m": None,
                    "entry_overspeed_kmh": round(entry_overspeed, 1),
                },
            ))

        # Poor corner exit: player is clearly slower out of the corner.
        exit_deficit = float(-exit_["delta_speed_kmh"].mean())
        if exit_deficit >= EXIT_SPEED_DEFICIT_KMH:
            errors.append(_make_error(
                f"{name}_poor_corner_exit", name, "poor_corner_exit",
                _severity(exit_deficit, EXIT_SPEED_DEFICIT_KMH),
                _confidence(exit_deficit, EXIT_SPEED_DEFICIT_KMH),
                ["throttle", "speed_kmh", "wheel_spin"],
                f"Poor corner exit detected at {name}.",
                f"Get on the throttle earlier and more progressively out of {name}; "
                f"you are exiting about {exit_deficit:.0f} km/h slower than the baseline.",
                {
                    "exit_speed_deficit_kmh": round(exit_deficit, 1),
                    "mean_throttle_gap": round(float(-exit_["delta_throttle"].mean()), 2),
                },
            ))

        # Off the racing line: sustained lateral gap to the expert's line.
        line_gap = float(mid["delta_track_pos"].abs().mean())
        if line_gap >= OFFLINE_TRACK_POS:
            errors.append(_make_error(
                f"{name}_poor_track_position", name, "poor_track_position",
                _severity(line_gap, OFFLINE_TRACK_POS), _confidence(line_gap, OFFLINE_TRACK_POS),
                ["track_pos", "angle", "steer"],
                f"The car is off the ideal racing line at {name}.",
                f"Follow the baseline line through {name} more closely and avoid large steering corrections.",
                {"mean_track_pos_gap": round(line_gap, 2)},
            ))

        # Unstable throttle: much jerkier pedal work than the expert.
        throttle_excess = float(mid["player_throttle"].std() - mid["expert_throttle"].std())
        if throttle_excess >= THROTTLE_STD_EXCESS:
            errors.append(_make_error(
                f"{name}_unstable_throttle", name, "unstable_throttle",
                _severity(throttle_excess, THROTTLE_STD_EXCESS),
                _confidence(throttle_excess, THROTTLE_STD_EXCESS),
                ["throttle", "wheel_spin", "speed_kmh"],
                f"Unstable throttle control detected at {name}.",
                f"Use one smooth throttle application through {name} instead of pumping the pedal.",
                {"throttle_std_excess": round(throttle_excess, 2)},
            ))
    return errors


def detect_sector_time_loss(deltas, track_length_m):
    """Split the lap into 3 sectors and report where lap time is lost."""
    errors = []
    boundaries = np.linspace(0.0, track_length_m, 4)
    for i in range(3):
        zone = _zone(deltas, boundaries[i], boundaries[i + 1])
        if zone.empty:
            continue
        loss = float(zone["delta_lap_time"].iloc[-1] - zone["delta_lap_time"].iloc[0])
        if loss >= SECTOR_TIME_LOSS_S:
            sector = f"Sector {i + 1}"
            errors.append(_make_error(
                f"S{i + 1}_time_loss", sector, "sector_time_loss",
                _severity(loss, SECTOR_TIME_LOSS_S), _confidence(loss, SECTOR_TIME_LOSS_S),
                [f"sector_{i + 1}", "lap_time", "speed_kmh"],
                f"Significant time loss detected in {sector}.",
                f"You lost about {loss:.1f} s to the baseline in {sector}; "
                f"focus on the corners between {boundaries[i]:.0f} m and {boundaries[i + 1]:.0f} m.",
                {
                    "time_loss_s": round(loss, 2),
                    "sector_start_m": round(float(boundaries[i]), 0),
                    "sector_end_m": round(float(boundaries[i + 1]), 0),
                },
            ))
    return errors


def detect_errors(deltas, corners, track_length_m):
    """Run all detectors and return errors sorted by severity, worst first."""
    errors = detect_corner_errors(deltas, corners) + detect_sector_time_loss(deltas, track_length_m)
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(errors, key=lambda e: (rank[e["severity"]], -e["confidence"]))
