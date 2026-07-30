"""One-command runner for the end-to-end latency test (Test Matrix #09).

Handles the src/ vs tests/ folder-crossing problem automatically, so you
don't have to manually cd back and forth or remember relative paths.

BEFORE running this: start the main system in ANOTHER terminal first,
so live_coach.py / ai_core.py / AudioManager are up and listening:

    cd "<project root>/src"
    python main.py

THEN, in a second terminal, from anywhere, run this script:

    python tests/integration/run_latency_test.py --fixture normal
    python tests/integration/run_latency_test.py --fixture many --repeats 8
    python tests/integration/run_latency_test.py --fixture final

Fixture shortcuts (--fixture):
    normal -> fixture_normal_lap.csv
    many   -> fixture_many_errors.csv
    final  -> fixture_final_lap.csv
Or pass any other filename directly, e.g. --fixture my_custom_case.csv

This script does NOT start main.py for you - that has to stay running
the whole time in its own terminal so AudioManager/ai_core stay alive
between replay runs.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# This file lives at <repo>/tests/integration/run_latency_test.py.
THIS_DIR = Path(__file__).resolve().parent          # tests/integration/
TESTS_DIR = THIS_DIR.parent                          # tests/
PROJECT_ROOT = TESTS_DIR.parent                      # repo root
SRC_DIR = PROJECT_ROOT / "src"                       # where analysis/ lives
FIXTURES_DIR = TESTS_DIR / "fixtures"

FIXTURE_SHORTCUTS = {
    "normal": "fixture_normal_lap.csv",
    "many": "fixture_many_errors.csv",
    "final": "fixture_final_lap.csv",
}

# latency_logger.py sits right next to this script, so a plain import
# works with no sys.path tricks needed.
sys.path.insert(0, str(THIS_DIR))
from latency_logger import reset_log  # noqa: E402


def resolve_fixture(name: str) -> Path:
    filename = FIXTURE_SHORTCUTS.get(name, name)
    path = FIXTURES_DIR / filename
    if not path.exists():
        raise SystemExit(
            f"Fixture not found: {path}\n"
            f"Run tests/integration/generate_fixtures.py first, or check the filename."
        )
    return path


def run_replay(fixture_path: Path, speed: float):
    """Runs `python -m analysis.replay <fixture>` with the correct
    working directory (src/), regardless of where this script was called
    from. Uses an ABSOLUTE path for the fixture so the working-directory
    switch never breaks the file lookup."""
    cmd = [sys.executable, "-m", "analysis.replay", str(fixture_path.resolve()), "--speed", str(speed)]
    print(f"\n$ (cwd={SRC_DIR}) {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(SRC_DIR))
    if result.returncode != 0:
        print(f"[warn] replay exited with code {result.returncode}")


def main():
    parser = argparse.ArgumentParser(description="Run the end-to-end latency test N times, then analyze.")
    parser.add_argument("--fixture", default="normal",
                        help="normal | many | final, or a filename in tests/fixtures/")
    parser.add_argument("--speed", type=float, default=1.0, help="Replay speed multiplier (default 1.0 = real time)")
    parser.add_argument("--repeats", type=int, default=8, help="How many times to replay this fixture")
    parser.add_argument("--pause", type=float, default=6.0,
                        help="Seconds to wait after each replay for the voice queue to finish draining")
    parser.add_argument("--no-clear", action="store_true",
                        help="Don't clear the log first (use this to add more samples to an existing run)")
    args = parser.parse_args()

    fixture_path = resolve_fixture(args.fixture)

    if not SRC_DIR.exists():
        raise SystemExit(f"Expected src/ folder at {SRC_DIR}, but it doesn't exist. "
                          f"Check PROJECT_ROOT/SRC_DIR assumptions at the top of this script.")

    print(f"Project root : {PROJECT_ROOT}")
    print(f"src/ dir     : {SRC_DIR}")
    print(f"Fixture      : {fixture_path}")
    print(f"Repeats      : {args.repeats}  (speed={args.speed}x, pause={args.pause}s between runs)")
    print("\nMake sure `python main.py` is already running in another terminal (from src/) "
          "before continuing.")
    input("Press Enter to start...")

    if not args.no_clear:
        reset_log()

    for i in range(1, args.repeats + 1):
        print(f"\n=== Run {i}/{args.repeats} ===")
        run_replay(fixture_path, args.speed)
        if i < args.repeats:
            print(f"Waiting {args.pause}s for the voice queue to drain before the next run...")
            time.sleep(args.pause)

    print("\nAll replays done. Running analysis...\n")
    subprocess.run([sys.executable, str(THIS_DIR / "analyze_latency.py")])


if __name__ == "__main__":
    main()