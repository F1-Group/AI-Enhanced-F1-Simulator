"""One-command demo of Team 2's Week 2 slow layer.

Runs the full pipeline on the latest player recording and then speaks the
top detected errors through the AudioManager - the same path Team 3's
Granite coaching audio will use.

Usage:
    .venv/bin/python -m analysis.demo
"""

import time

from .lap_utils import load_telemetry, split_laps
from .run_analysis import DEFAULT_EXPERT, analyse_lap, latest_player_file

DEMO_ERROR_COUNT = 3


def main():
    player_path = latest_player_file()
    expert_laps = split_laps(load_telemetry(DEFAULT_EXPERT))
    player_lap = split_laps(load_telemetry(player_path))[0]

    print(f"Comparing {player_path.name} against the expert baseline...\n")
    report, _ = analyse_lap(player_lap, expert_laps)

    print(f"Lap time: {report['player_lap_time_s']}s "
          f"(expert: {report['expert_lap_time_s']}s) | "
          f"{len(report['corners_detected'])} corners | "
          f"{len(report['errors'])} errors detected\n")
    for error in report["errors"]:
        print(f"  [{error['severity']:>6}] {error['tag']:<28} {error['message']}")

    # Speak the top errors, one per error type so the demo stays varied
    # (the AudioManager's cooldown would skip repeats of the same clip).
    from audio_manager.audio_manager import AudioManager

    manager = AudioManager()
    played_types = set()
    print(f"\nPlaying the top {DEMO_ERROR_COUNT} coaching alerts...")
    for error in report["errors"]:
        if error["type"] in played_types:
            continue
        manager.play_error(error)
        played_types.add(error["type"])
        time.sleep(4)
        if len(played_types) >= DEMO_ERROR_COUNT:
            break

    time.sleep(2)
    manager.shutdown()
    print("Demo finished.")


if __name__ == "__main__":
    main()
