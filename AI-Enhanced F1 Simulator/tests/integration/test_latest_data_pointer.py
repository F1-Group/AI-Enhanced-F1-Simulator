import os
import time
import pytest
from pathlib import Path
from unittest.mock import patch

from data_pipeline.logger import CSVLogger
from analysis.run_analysis import latest_player_file
from analysis.live_coach import wait_for_recording


@pytest.fixture
def temp_environment(tmp_path):
    """Set up temporary directory and pointer file path."""
    output_dir = tmp_path / "player_data"
    output_dir.mkdir()
    pointer_file = tmp_path / "latest_data.txt"
    return output_dir, pointer_file


def test_latest_pointer_contract(temp_environment):
    """Verify that the latest_data.txt path written by CSVLogger.create_file() 
    can be successfully read by run_analysis.latest_player_file().
    """
    output_dir, pointer_file = temp_environment

    # Patch LATEST_POINTER in both modules to point to the test-specific temporary pointer file
    with patch("data_pipeline.logger.LATEST_POINTER", pointer_file), \
         patch("analysis.run_analysis.LATEST_POINTER", pointer_file):

        # Create the first file
        logger1 = CSVLogger(output_dir=output_dir)
        logger1.create_file()
        first_file_path = Path(logger1.filename).resolve()

        resolved_file_1 = latest_player_file()
        assert resolved_file_1 == first_file_path

        time.sleep(1.1)

        # Create the second file
        logger2 = CSVLogger(output_dir=output_dir)
        logger2.create_file()
        second_file_path = Path(logger2.filename).resolve()

        # Verify run_analysis automatically switches to logger2's new file
        resolved_file_2 = latest_player_file()
        assert resolved_file_2 == second_file_path
        assert resolved_file_2 != first_file_path

        # Clean up resources
        logger1.close()
        logger2.close()

def test_wait_for_recording_mtime_freshness_check(tmp_path):
    """Verify mtime freshness check in live_coach.py's wait_for_recording()."""
    pointer_file = tmp_path / "latest_data.txt"
    old_csv = tmp_path / "old_telemetry.csv"
    fresh_csv = tmp_path / "fresh_telemetry.csv"

    old_csv.write_text("lap_time,speed_kmh\n1.0,100.0\n", encoding="utf-8")
    
    # Modify old_csv's mtime to 20 seconds ago
    past_mtime = time.time() - 20.0
    os.utime(old_csv, (past_mtime, past_mtime))

    # Point pointer file to the expired CSV
    pointer_file.write_text(str(old_csv.resolve()), encoding="utf-8")

    with patch("analysis.live_coach.LATEST_POINTER", pointer_file):
        # Since old_csv is expired (>10s), wait_for_recording will wait until timeout and raise SystemExit
        with pytest.raises(SystemExit) as exc_info:
            wait_for_recording(timeout_s=0.5, freshness_s=10.0)
        assert "No active recording appeared" in str(exc_info.value)

        # Update pointer to the freshly generated fresh_csv
        fresh_csv.write_text("lap_time,speed_kmh\n1.0,105.0\n", encoding="utf-8")
        pointer_file.write_text(str(fresh_csv.resolve()), encoding="utf-8")

        # The fresh file successfully passes the mtime check and returns its absolute path
        resolved_path = wait_for_recording(timeout_s=1.0, freshness_s=10.0)
        assert resolved_path == fresh_csv.resolve()