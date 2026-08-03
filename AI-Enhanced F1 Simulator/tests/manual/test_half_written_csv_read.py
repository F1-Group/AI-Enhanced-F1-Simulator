"""Manual check for Test Matrix #23: "Reading a CSV while it is half-written"
(Team1 logger.py writing <-> live_coach.py's _read_complete_line() reading).

Question this answers: when live_coach.py's follow() loop reads a telemetry
CSV that another process is actively appending to, can it ever see a
half-written row (the writer flushed "12.3,45.6,7" but hasn't yet written
the rest of the line + "\\n")? If so, does it crash, or silently misparse
that partial row into a corrupt/short frame - or does the "half-line
protection" (seek back, retry next poll) in _read_complete_line() actually
prevent that?

Two checks:
    1. Unit-level: feed _read_complete_line() a file with one complete line
       and one line deliberately missing its trailing newline, and confirm
       it returns the complete line, returns None (not a broken partial
       string) for the incomplete one, and leaves the file position so the
       same bytes are re-read once the rest arrives.
    2. Race-condition level: a real writer thread appends a CSV in small,
       deliberately-interrupted chunks (splitting each row into two writes
       with a short random delay between them, then a flush) while this
       process concurrently follows the file with the SAME read+parse logic
       live_coach.py's follow() uses. Runs many rows so the reader is
       virtually guaranteed to hit the writer mid-row at least once, then
       asserts every row it eventually produced is complete, correctly
       typed, and that no row was ever lost, duplicated, or corrupted.

Run directly:
    python tests/manual/test_half_written_csv_read.py

Or via pytest:
    pytest tests/manual/test_half_written_csv_read.py -v -s

Uses only a temp file it creates and cleans up itself - no recorded data or
local-only files needed.
"""

import random
import sys
import tempfile
import threading
import time
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[2]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from analysis.live_coach import POLL_S, _read_complete_line  # noqa: E402

# Same 13-field schema run_analysis.py documents as "exactly what Team 3's
# build_user_prompt(telemetry, question) expects" (TELEMETRY_FIELDS).
HEADER_FIELDS = ["lap_time", "lap_distance", "speed_kmh", "track_pos", "angle",
                 "wheel_spin", "gear", "rpm", "race_pos", "fuel",
                 "throttle", "brake", "steer"]

NUM_ROWS = 400          # enough rows that a mid-row read is virtually guaranteed
MAX_SPLIT_DELAY_S = 0.003  # tiny random delay between a row's two write() calls


def test_read_complete_line_defers_a_line_missing_its_newline():
    """Unit-level check of _read_complete_line() itself, with no concurrency
    involved - isolates the exact seek-back/retry behaviour."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "growing.csv"
        path.write_text("1.0,2.0,3.0\n4.0,5.0,6", encoding="utf-8")  # 2nd line has no \n yet

        with open(path, encoding="utf-8") as f:
            first = _read_complete_line(f)
            assert first == "1.0,2.0,3.0", f"expected the complete first line, got {first!r}"

            pos_before_retry = f.tell()  # position where the incomplete 2nd line starts
            second_attempt = _read_complete_line(f)
            assert second_attempt is None, (
                f"a line with no trailing newline yet must return None (not a partial string), got {second_attempt!r}"
            )
            assert f.tell() == pos_before_retry, (
                "file position was not correctly rewound - the partial bytes would be lost, not re-read"
            )

            # The "rest of the write" arrives.
            with open(path, "a", encoding="utf-8") as writer:
                writer.write(",7.0\n")

            completed = _read_complete_line(f)
            assert completed == "4.0,5.0,6,7.0", f"expected the now-completed line, got {completed!r}"

    print("\n[unit] _read_complete_line() correctly deferred a not-yet-terminated line and "
          "re-read it whole once it was finished")


def _write_rows_with_torn_writes(path, num_rows, header_fields, stop_flag):
    """Simulates Team1's logger: writes the header, then each data row split
    into two separate write()+flush() calls with a short random gap between
    them - deliberately giving a concurrent reader a window where the file
    ends mid-row."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(",".join(header_fields) + "\n")
        f.flush()

        for i in range(num_rows):
            values = [f"{(i + 1) * (j + 1) + 0.1 * j:.3f}" for j in range(len(header_fields))]
            line = ",".join(values)
            split_at = random.randint(1, len(line) - 1)

            f.write(line[:split_at])
            f.flush()
            time.sleep(random.uniform(0, MAX_SPLIT_DELAY_S))
            f.write(line[split_at:] + "\n")
            f.flush()

    stop_flag["writer_done"] = True


def test_concurrent_read_of_a_growing_csv_never_sees_a_torn_row():
    """Real writer thread + real concurrent reader loop (mirroring
    live_coach.py's follow() read+parse logic), stressing the exact race
    Test Matrix #23 is worried about."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "growing.csv"
        path.touch()

        stop_flag = {"writer_done": False}
        writer_thread = threading.Thread(
            target=_write_rows_with_torn_writes, args=(path, NUM_ROWS, HEADER_FIELDS, stop_flag), daemon=True,
        )
        writer_thread.start()

        frames = []
        malformed_lines = []
        header = None
        with open(path, encoding="utf-8") as f:
            deadline = time.time() + 30.0
            while len(frames) < NUM_ROWS and time.time() < deadline:
                line = _read_complete_line(f)
                if line is None:
                    if stop_flag["writer_done"] and len(frames) >= NUM_ROWS:
                        break
                    time.sleep(POLL_S)
                    continue

                if header is None:
                    header = line.split(",")
                    continue

                # Mirrors live_coach.py's follow() parsing exactly (see
                # analysis/live_coach.py's frame-building block).
                parts = line.split(",")
                if len(parts) != len(header):
                    malformed_lines.append(line)
                    continue

                frame = {}
                for key, value in zip(header, parts):
                    try:
                        frame[key] = float(value)
                    except ValueError:
                        malformed_lines.append(line)
                        break
                else:
                    frames.append(frame)

        writer_thread.join(timeout=5.0)

        assert not malformed_lines, (
            f"{len(malformed_lines)} line(s) were seen as wrong-length or non-numeric "
            f"(a torn row slipped through the half-line protection): {malformed_lines[:5]}"
        )
        assert len(frames) == NUM_ROWS, (
            f"expected exactly {NUM_ROWS} complete frames, got {len(frames)} "
            f"(rows were lost or duplicated across the read/write race)"
        )
        assert all(len(frame) == len(HEADER_FIELDS) for frame in frames), (
            "at least one frame ended up with the wrong number of fields"
        )

    print(f"\n[race] read {len(frames)}/{NUM_ROWS} rows from a concurrently-written, "
          f"deliberately torn-mid-row CSV with zero malformed lines and zero crashes")


if __name__ == "__main__":
    print("=== Test Matrix #23: reading a CSV while it is half-written ===")
    for test in (test_read_complete_line_defers_a_line_missing_its_newline,
                 test_concurrent_read_of_a_growing_csv_never_sees_a_torn_row):
        try:
            test()
            print(f"PASS: {test.__name__}\n")
        except AssertionError as exc:
            print(f"FAIL: {test.__name__}: {exc}\n")
